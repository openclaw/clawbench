"""OpenClaw adapter — drives tasks through an OpenClaw gateway.

This is the adapter-shaped wrapper around the agent execution flow that
has lived inside `BenchmarkHarness._run_single` until now. It holds a
`GatewayClient` open for the run's duration, creates one agent per run
and one session per phase (matching the existing behavior), delivers
simulated-user turns, and resolves `StateQuery` assertions against the
gateway's `memory.search` / `sessions.resolve` / `cron.list` / arbitrary
`_rpc(method)` surface.

The legacy harness still owns the executable CLI path for now; this
adapter is the canonical wrapper used by adapter-level tests and later
harness wiring.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass

from clawbench.adapters import register_adapter
from clawbench.adapters.base import (
    AdapterConfig,
    AdapterContext,
    AgentAdapter,
    PhaseResult,
    StateQueryResult,
)
from clawbench.canonical import (
    AdapterCapability,
    CanonicalPhase,
    StateQuery,
)
from clawbench.client import GatewayClient, GatewayConfig
from clawbench.environment_files import (
    resolve_json_path,
    verify_memory_fallback,
)
from clawbench.schemas import (
    MemoryState,
    PromptVariant,
)
from clawbench.session_labels import unique_session_label
from clawbench.simulated_user import UserSimulator

logger = logging.getLogger(__name__)


@dataclass
class OpenClawAdapterConfig(AdapterConfig):
    """Config for the OpenClaw adapter.

    `gateway` holds the connection parameters the adapter uses to reach
    the OpenClaw gateway. `prompt_variant` controls which wording of
    each simulated-user turn is rendered.
    """

    gateway: GatewayConfig | None = None
    prompt_variant: str = PromptVariant.CLEAR.value
    # Default per-turn timeout passed to `send_and_wait` when the
    # phase does not override it. Matches the existing harness default.
    turn_timeout_seconds: float = 180.0


@register_adapter
class OpenClawAdapter(AgentAdapter):
    """Adapter for the OpenClaw gateway (default harness path)."""

    name = "openclaw"
    capabilities = {
        AdapterCapability.FILES,
        AdapterCapability.EXECUTION,
        AdapterCapability.MEMORY,
        AdapterCapability.SESSION,
        AdapterCapability.CRON,
        AdapterCapability.BROWSER,
        AdapterCapability.GATEWAY_RPC,
        AdapterCapability.MULTI_TURN_INJECTION,
    }

    def __init__(self, config: OpenClawAdapterConfig | None = None) -> None:
        super().__init__(config or OpenClawAdapterConfig())
        self._config: OpenClawAdapterConfig = self.config  # type: ignore[assignment]
        self._gateway_config: GatewayConfig = self._config.gateway or GatewayConfig()
        self._client: GatewayClient | None = None
        # Dependency injection hook for tests: monkeypatch this to swap
        # in a stub gateway without touching the class definition.
        self._client_factory = lambda: GatewayClient(self._gateway_config)

    # ------------------------------------------------------------------
    # Long-lived gateway connection.
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "OpenClawAdapter":
        client = self._client_factory()
        await client.__aenter__()
        self._client = client
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._client is not None:
            try:
                await self._client.__aexit__(exc_type, exc, tb)
            finally:
                self._client = None

    @property
    def client(self) -> GatewayClient:
        if self._client is None:
            raise RuntimeError(
                "OpenClawAdapter must be used as an async context manager "
                "before calling setup/run_phase/teardown."
            )
        return self._client

    # ------------------------------------------------------------------
    # Lifecycle.
    # ------------------------------------------------------------------

    async def setup(self, ctx: AdapterContext) -> None:
        """Create the per-run agent and run pre-run state queries."""

        self._realize_memory_seeds(ctx)

        agent_name = (
            f"clawbench-{ctx.task.id}-run-{ctx.run_index}-{uuid.uuid4().hex[:6]}"
        )
        agent_id = await self.client.create_agent(
            name=agent_name, workspace=str(ctx.workspace)
        )
        ctx.adapter_state["agent_id"] = agent_id
        ctx.adapter_state.setdefault("session_keys", [])

        # Pre-run gateway assertions (ex-`setup.pre_check_gateway`) —
        # evaluated immediately, failures are surfaced via the returned
        # state via `ctx.adapter_state["pre_run_failures"]` so the
        # harness can fail fast before doing any phase work.
        failures: list[str] = []
        for query in ctx.task.verifier.pre_run_queries:
            result = await self.verify_state_query(query, ctx)
            if not result.ok:
                failures.append(result.detail or query.description)
        if failures:
            ctx.adapter_state["pre_run_failures"] = failures

    def _realize_memory_seeds(self, ctx: AdapterContext) -> None:
        """Expose canonical memory seeds through the run workspace.

        OpenClaw's native memory backend has no public seed/write RPC in the
        benchmark client, but agents can read files in their workspace and the
        verifier already falls back to these same memory files. This keeps
        seeded-memory tasks fair across OpenClaw and filesystem-first harnesses.
        """

        chunks: list[str] = []
        for seed in ctx.task.assets.seed_state:
            if seed.kind != "memory" or not seed.key:
                continue
            content = seed.content or ""
            if not isinstance(content, str):
                content = str(content)
            safe_key = "".join(
                ch if ch.isalnum() or ch in ("-", "_") else "_"
                for ch in seed.key.strip()
            ).strip("_")
            if not safe_key:
                safe_key = "seed"
            body = f"# {seed.key}\n\n{content.strip()}\n"
            target = ctx.workspace / "memory" / f"{safe_key}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
            chunks.append(body)

        if chunks:
            (ctx.workspace / "MEMORY.md").write_text("\n".join(chunks), encoding="utf-8")

    async def run_phase(
        self,
        phase: CanonicalPhase,
        ctx: AdapterContext,
    ) -> PhaseResult:
        """Create a session, drive the simulator, append to the transcript."""

        agent_id = ctx.adapter_state.get("agent_id")
        if not agent_id:
            return PhaseResult(
                error="OpenClawAdapter.run_phase called before setup(); no agent_id",
                completed_normally=False,
            )

        session_keys: list[str] = ctx.adapter_state.setdefault("session_keys", [])
        session_key = await self.client.create_session(
            model=ctx.model,
            agent_id=agent_id,
            label=unique_session_label(
                f"clawbench-{ctx.task.id}-run{ctx.run_index}-phase{phase.name}"
            ),
        )
        session_keys.append(session_key)
        ctx.adapter_state["last_session_key"] = session_key

        await self.client.subscribe(session_key)

        # Browser tasks require the browser tool to actually be
        # registered in the effective tool set for this session. If it
        # isn't, fail the phase fast rather than letting the agent
        # flounder against a missing tool.
        if ctx.task.family.value == "browser":
            try:
                await self._assert_browser_support(session_key)
            except Exception as exc:
                return PhaseResult(
                    error=str(exc),
                    completed_normally=False,
                )

        simulator = UserSimulator(
            phase.user,
            ctx.runtime_values,
            prompt_variant=self._config.prompt_variant,
        )

        turn_timeout = float(phase.timeout_seconds or ctx.task.budgets.timeout_seconds)
        turn_timeout = min(turn_timeout, self._config.turn_timeout_seconds)

        appended: list = []
        turns_sent = 0
        while not simulator.is_done:
            user_message = await simulator.next_message(ctx.transcript)
            if user_message is None:
                break
            phase_transcript = await self.client.send_and_wait(
                session_key,
                user_message,
                timeout=turn_timeout,
            )
            ctx.transcript.messages.extend(phase_transcript.messages)
            appended.extend(phase_transcript.messages)
            turns_sent += 1

        return PhaseResult(
            messages=appended,
            adapter_metadata={
                "session_key": session_key,
                "turns_sent": turns_sent,
            },
        )

    async def _assert_browser_support(self, session_key: str) -> None:
        inventory = await self.client.get_effective_tools(session_key)
        tool_ids = {
            str(tool.get("id", ""))
            for group in inventory.get("groups", [])
            for tool in group.get("tools", [])
        }
        if "browser" not in tool_ids:
            raise RuntimeError(
                "Browser tasks require the browser tool, but it is not available in this gateway."
            )

    async def teardown(self, ctx: AdapterContext) -> None:
        """Delete per-phase sessions and the per-run agent."""

        client = self._client
        if client is None:
            return
        session_keys: list[str] = ctx.adapter_state.get("session_keys", [])
        agent_id: str | None = ctx.adapter_state.get("agent_id")
        for session_key in session_keys:
            try:
                await client.delete_session(session_key)
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("delete_session failed for %s: %s", session_key, exc)
        if agent_id:
            try:
                await client.delete_agent(agent_id, delete_files=False)
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("delete_agent failed for %s: %s", agent_id, exc)

    # ------------------------------------------------------------------
    # State query resolution.
    # ------------------------------------------------------------------

    async def verify_state_query(
        self,
        query: StateQuery,
        ctx: AdapterContext,
    ) -> StateQueryResult:
        try:
            if query.kind == "memory":
                return await self._verify_memory(query, ctx)
            if query.kind == "session":
                return await self._verify_session(query, ctx)
            if query.kind == "cron":
                return await self._verify_cron(query, ctx)
            if query.kind == "custom":
                return await self._verify_gateway(query, ctx)
        except Exception as exc:
            return StateQueryResult(ok=False, detail=str(exc))
        return StateQueryResult(
            ok=False,
            detail=f"OpenClawAdapter has no handler for query kind '{query.kind}'",
            capability_missing=True,
        )

    # --- memory ---

    async def _verify_memory(
        self, query: StateQuery, ctx: AdapterContext
    ) -> StateQueryResult:
        key_pattern = str(query.selector.get("key_pattern", ""))
        value_contains = list(query.expected.get("value_contains", []))
        session_key = ctx.adapter_state.get("last_session_key", "")
        agent_id = ctx.adapter_state.get("agent_id")

        # Primary path: memory.search RPC.
        try:
            response = await self.client._rpc(
                "memory.search",
                {
                    "query": key_pattern,
                    "sessionKey": session_key,
                    "limit": 20,
                },
            )
            entries = response.get("payload", {}).get("entries", [])
            if query.predicate == "absent":
                ok = not entries
                return StateQueryResult(
                    ok=ok,
                    detail="Correctly absent" if ok else "Memory entry exists",
                )
            if not entries:
                return StateQueryResult(ok=False, detail="No matching memory entries found")
            all_values = " ".join(str(entry.get("value", "")) for entry in entries)
            for token in value_contains:
                if token.lower() not in all_values.lower():
                    return StateQueryResult(
                        ok=False, detail=f"Memory value missing '{token}'"
                    )
            return StateQueryResult(ok=True, detail="OK")
        except Exception as exc:
            logger.info(
                "memory.search unavailable for verification, falling back: %s",
                exc,
            )

        # Fallback: gateway-sourced memory files + workspace scan + transcript.
        fallback_state = MemoryState(
            key_pattern=key_pattern,
            exists=query.predicate != "absent",
            value_contains=value_contains,
        )
        extra_memory_text = ""
        if agent_id:
            try:
                from clawbench.environment import _read_agent_memory_text  # local import to avoid cycle

                extra_memory_text = await _read_agent_memory_text(self.client, agent_id)
            except Exception:
                extra_memory_text = ""
        ok, detail = verify_memory_fallback(
            fallback_state,
            ctx.workspace,
            transcript=ctx.transcript,
            extra_memory_text=extra_memory_text,
        )
        return StateQueryResult(ok=ok, detail=detail)

    # --- session ---

    async def _verify_session(
        self, query: StateQuery, ctx: AdapterContext
    ) -> StateQueryResult:
        session_key = ctx.adapter_state.get("last_session_key", "")
        expected_model = query.expected.get("model") or ""
        try:
            response = await self.client._rpc("sessions.resolve", {"key": session_key})
            payload = response.get("payload", {})
            if query.predicate == "absent":
                return StateQueryResult(ok=False, detail="Session exists but should not")
            if expected_model:
                actual = str(payload.get("model", ""))
                if str(expected_model).lower() not in actual.lower():
                    return StateQueryResult(
                        ok=False,
                        detail=f"Model mismatch: expected {expected_model}, got {actual}",
                    )
            return StateQueryResult(ok=True, detail="OK")
        except Exception as exc:
            if query.predicate == "absent":
                return StateQueryResult(ok=True, detail="Correctly absent")
            return StateQueryResult(ok=False, detail=str(exc))

    # --- cron ---

    async def _verify_cron(
        self, query: StateQuery, ctx: AdapterContext
    ) -> StateQueryResult:
        description_contains = query.selector.get("description_contains")
        try:
            response = await self.client._rpc("cron.list", {})
            jobs = response.get("payload", {}).get("jobs", [])
            if query.predicate == "absent":
                ok = not jobs
                return StateQueryResult(
                    ok=ok,
                    detail="Correctly absent" if ok else "Cron jobs exist",
                )
            if not jobs:
                return StateQueryResult(ok=False, detail="No cron jobs found")
            if description_contains and not any(
                str(description_contains).lower() in json.dumps(job).lower() for job in jobs
            ):
                return StateQueryResult(
                    ok=False,
                    detail=f"No cron job matched '{description_contains}'",
                )
            return StateQueryResult(ok=True, detail="OK")
        except Exception as exc:
            return StateQueryResult(ok=False, detail=str(exc))

    # --- arbitrary gateway RPC ---

    async def _verify_gateway(
        self, query: StateQuery, ctx: AdapterContext
    ) -> StateQueryResult:
        method = str(query.selector.get("method", ""))
        params = dict(query.selector.get("params", {}))
        assert_path = str(query.selector.get("assert_path", "$"))
        expected_equals = query.expected.get("equals")
        expected_contains = query.expected.get("contains")
        expected_exists = bool(query.expected.get("exists", True))
        try:
            response = await self.client._rpc(method, params)
            payload = response.get("payload", {})
            value = resolve_json_path(payload, assert_path)
            if not expected_exists:
                ok = value is None
                return StateQueryResult(
                    ok=ok,
                    detail="Correctly absent" if ok else "Path exists",
                )
            if value is None:
                return StateQueryResult(
                    ok=False, detail=f"Path {assert_path} not found"
                )
            if expected_equals is not None and value != expected_equals:
                return StateQueryResult(
                    ok=False, detail=f"Expected {expected_equals}, got {value}"
                )
            if (
                expected_contains is not None
                and str(expected_contains).lower() not in str(value).lower()
            ):
                return StateQueryResult(
                    ok=False,
                    detail=f"Expected '{expected_contains}' in {value}",
                )
            return StateQueryResult(ok=True, detail="OK")
        except Exception as exc:
            return StateQueryResult(ok=False, detail=str(exc))


__all__ = ["OpenClawAdapter", "OpenClawAdapterConfig"]
