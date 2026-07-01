"""Hermes adapter — drives Nous Research `hermes-agent`.

Hermes (https://github.com/NousResearch/hermes-agent) is a Python agent
framework with `MiniSWERunner` as its clean programmatic entry point.
This adapter:

1. Realizes the canonical workspace + seed state (seed_state entries
   with `kind="memory"` become files, since Hermes has no memory RPC).
2. Constructs a `MiniSWERunner` scoped to the workspace.
3. For each canonical phase, renders the user turn and calls
   `runner.run_task(prompt)` in a worker thread, with the phase's
   timeout enforced as a wall clock.
4. Parses the returned `conversations` via
   `clawbench.adapters.hermes_xml.parse_conversation` into a canonical
   `Transcript` the scorer can consume unchanged.
5. For state queries the adapter can't resolve (session, cron, custom
   gateway RPC), returns `capability_missing=True` so the harness
   reports a clean skip. Memory queries fall back to workspace file
   scanning via `environment_files.verify_memory_fallback`.

`hermes-agent` is an **optional** dependency (`clawbench[hermes]`). The
import is guarded so the base install stays lean; calling this adapter
without the dep installed raises a clear error rather than a cryptic
`ImportError`.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from clawbench.adapters import register_adapter
from clawbench.adapters.base import (
    AdapterConfig,
    AdapterContext,
    AgentAdapter,
    PhaseResult,
    StateQueryResult,
)
from clawbench.adapters.hermes_xml import parse_chat_messages, parse_conversation
from clawbench.canonical import (
    AdapterCapability,
    CanonicalPhase,
    StateQuery,
)
from clawbench.environment_files import verify_memory_fallback
from clawbench.render import render_template
from clawbench.schemas import MemoryState, PromptVariant
from clawbench.simulated_user import UserSimulator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Optional dependency import — guarded so the base install stays lean.
# ---------------------------------------------------------------------------

def _load_mini_swe_runner() -> tuple[Any, Exception | None]:
    try:  # pragma: no cover - import-guard branch
        from mini_swe_runner import MiniSWERunner as runner_cls  # type: ignore[import-not-found]

        return runner_cls, None
    except Exception as exc:  # pragma: no cover - import-guard branch
        import_error = exc
        candidates: list[Path] = []
        explicit_file = os.environ.get("HERMES_MINI_SWE_RUNNER")
        if explicit_file:
            candidates.append(Path(explicit_file).expanduser())
        for env_name in ("HERMES_AGENT_REPO", "HERMES_INSTALL_DIR"):
            value = os.environ.get(env_name)
            if value:
                candidates.append(Path(value).expanduser() / "mini_swe_runner.py")
        hermes_home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
        candidates.append(hermes_home / "hermes-agent" / "mini_swe_runner.py")

        for path in candidates:
            if not path.is_file():
                continue
            try:
                repo_root = str(path.parent)
                if repo_root not in sys.path:
                    sys.path.insert(0, repo_root)
                spec = importlib.util.spec_from_file_location(
                    "_clawbench_hermes_mini_swe_runner",
                    path,
                )
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                return module.MiniSWERunner, None
            except Exception as path_exc:
                import_error = path_exc
                continue
        return None, import_error


MiniSWERunner, _HERMES_IMPORT_ERROR = _load_mini_swe_runner()


def _load_ai_agent() -> tuple[Any, Exception | None]:
    try:  # pragma: no cover - import-guard branch
        from run_agent import AIAgent as agent_cls  # type: ignore[import-not-found]

        return agent_cls, None
    except Exception as exc:  # pragma: no cover - import-guard branch
        import_error = exc
        candidates: list[Path] = []
        for env_name in ("HERMES_AGENT_REPO", "HERMES_INSTALL_DIR"):
            value = os.environ.get(env_name)
            if value:
                candidates.append(Path(value).expanduser() / "run_agent.py")
        hermes_home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
        candidates.append(hermes_home / "hermes-agent" / "run_agent.py")

        for path in candidates:
            if not path.is_file():
                continue
            try:
                repo_root = str(path.parent)
                if repo_root not in sys.path:
                    sys.path.insert(0, repo_root)
                spec = importlib.util.spec_from_file_location(
                    "_clawbench_hermes_run_agent",
                    path,
                )
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                return module.AIAgent, None
            except Exception as path_exc:
                import_error = path_exc
                continue
        return None, import_error


AIAgent, _HERMES_AGENT_IMPORT_ERROR = _load_ai_agent()


class _CodexToolMessageCompatClient:
    """Client wrapper for Hermes's Codex Responses shim.

    The current Hermes MiniSWERunner feeds OpenAI chat-style `role="tool"`
    messages back into `chat.completions.create()`. Hermes's Codex
    Responses adapter accepts chat-shaped calls but currently forwards
    those tool messages to Responses as plain input items, where Codex
    rejects the unsupported role. Rewriting tool results as user-visible
    text preserves the important observation for the next turn and keeps
    the runner moving.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.chat = _CodexToolMessageCompatChat(inner.chat)
        self.api_key = getattr(inner, "api_key", None)
        self.base_url = getattr(inner, "base_url", None)

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()


class _CodexToolMessageCompatChat:
    def __init__(self, inner_chat: Any) -> None:
        self.completions = _CodexToolMessageCompatCompletions(inner_chat.completions)


class _CodexToolMessageCompatCompletions:
    def __init__(self, inner_completions: Any) -> None:
        self._inner = inner_completions

    def create(self, **kwargs: Any) -> Any:
        messages = kwargs.get("messages")
        if isinstance(messages, list):
            kwargs = dict(kwargs)
            kwargs["messages"] = [_rewrite_codex_tool_message(message) for message in messages]
        return self._inner.create(**kwargs)


def _rewrite_codex_tool_message(message: Any) -> Any:
    if not isinstance(message, dict) or message.get("role") != "tool":
        return message
    content = message.get("content", "")
    if not isinstance(content, str):
        content = str(content)
    tool_call_id = message.get("tool_call_id") or message.get("name") or "tool"
    return {
        "role": "user",
        "content": f"Tool result ({tool_call_id}):\n{content}",
    }


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class HermesAdapterConfig(AdapterConfig):
    """Config for the Hermes adapter.

    Fields map onto `MiniSWERunner` kwargs; ClawBench passes the
    canonical model string through verbatim so users pick Hermes-
    supported models via the existing `--model` flag.
    """

    env_type: str = "local"
    max_iterations: int = 15
    timeout_seconds: int = 60
    base_url: str | None = None
    api_key: str | None = None
    provider: str | None = None
    api_mode: str | None = None
    prompt_variant: str = PromptVariant.CLEAR.value
    driver_mode: str = "mini_swe"
    enabled_toolsets: list[str] | None = None
    disabled_toolsets: list[str] | None = None
    hermes_home: str | None = None
    tool_delay_seconds: float = 0.0
    # Optional: an explicit `MiniSWERunner` factory. Used by tests to
    # plug in a stub; production code leaves this None and the adapter
    # instantiates the real runner lazily.
    runner_factory: Any = None
    agent_factory: Any = None


@register_adapter
class HermesAdapter(AgentAdapter):
    """Adapter for the Nous Research hermes-agent."""

    name = "hermes"
    capabilities = {
        AdapterCapability.FILES,
        AdapterCapability.EXECUTION,
    }

    @classmethod
    def supported_capabilities(cls, config: AdapterConfig | None = None) -> set[AdapterCapability]:
        if isinstance(config, HermesAdapterConfig) and config.driver_mode == "ai_agent":
            return {
                AdapterCapability.FILES,
                AdapterCapability.EXECUTION,
                AdapterCapability.MEMORY,
                AdapterCapability.CRON,
                AdapterCapability.BROWSER,
                AdapterCapability.MULTI_TURN_INJECTION,
            }
        return set(cls.capabilities)

    def __init__(self, config: HermesAdapterConfig | None = None) -> None:
        super().__init__(config or HermesAdapterConfig())
        self._config: HermesAdapterConfig = self.config  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Lifecycle.
    # ------------------------------------------------------------------

    async def setup(self, ctx: AdapterContext) -> None:
        """Realize memory seed state as files and build the runner.

        Hermes-in-`env_type=local` operates directly on the workspace
        filesystem, so memory `SeedEntry` entries are written out as
        `memory/<key>.md` files. Callers that want a different mapping
        can pre-populate the workspace before invoking the adapter.
        """

        for seed in ctx.task.assets.seed_state:
            if seed.kind == "memory" and seed.key:
                target = ctx.workspace / "memory" / f"{seed.key}.md"
                target.parent.mkdir(parents=True, exist_ok=True)
                content = seed.content or ""
                if not isinstance(content, str):
                    content = str(content)
                target.write_text(content, encoding="utf-8")

        if self._config.driver_mode == "ai_agent":
            agent = self._build_ai_agent(ctx)
            ctx.adapter_state["agent"] = agent
            ctx.adapter_state["conversation_history"] = []
            ctx.adapter_state["hermes_home"] = self._hermes_home(ctx)
        else:
            runner = self._build_runner(ctx)
            ctx.adapter_state["runner"] = runner
        ctx.adapter_state.setdefault("api_calls", 0)

    def _hermes_home(self, ctx: AdapterContext) -> Path:
        configured = self._config.hermes_home
        if configured:
            return Path(configured).expanduser()
        return ctx.workspace / ".hermes"

    def _prepare_process_env(self, ctx: AdapterContext) -> None:
        hermes_home = self._hermes_home(ctx)
        hermes_home.mkdir(parents=True, exist_ok=True)
        os.environ["HERMES_HOME"] = str(hermes_home)
        os.environ["TERMINAL_CWD"] = str(ctx.workspace)
        os.environ.setdefault("TERMINAL_ENV", "local")
        cron_jobs = sys.modules.get("cron.jobs")
        if cron_jobs is not None:
            cron_dir = hermes_home / "cron"
            setattr(cron_jobs, "HERMES_DIR", hermes_home)
            setattr(cron_jobs, "CRON_DIR", cron_dir)
            setattr(cron_jobs, "JOBS_FILE", cron_dir / "jobs.json")
            setattr(cron_jobs, "OUTPUT_DIR", cron_dir / "output")

    def _effective_model(self, ctx: AdapterContext) -> str:
        """Translate ClawBench provider-prefixed slugs for direct providers."""

        model = ctx.model
        if self._config.provider:
            return model
        base_url = self._config.base_url or ""
        try:
            host = urlparse(base_url).hostname or ""
        except Exception:
            host = ""
        if host == "api.openai.com" and model.startswith("openai/"):
            return model.split("/", 1)[1]
        return model

    def _runtime_provider_hint(self) -> str | None:
        """Return the provider identity Hermes should expose to its runtime.

        Hermes distinguishes the transport used for the main model from the
        auxiliary routing metadata it exposes to side tasks. Direct
        OpenAI-compatible endpoints need to keep their explicit base URL and
        API key, but should still identify as ``custom`` so Hermes auxiliary
        calls resolve to the same primary model instead of falling through to
        auto-detected providers such as OpenRouter.
        """

        if self._config.provider:
            return self._config.provider
        if self._config.base_url:
            return "custom"
        return None

    def _build_runner(self, ctx: AdapterContext) -> Any:
        explicit_api_key = None if self._config.provider else self._config.api_key
        explicit_base_url = None if self._config.provider else self._config.base_url
        effective_model = self._effective_model(ctx)
        ctx.adapter_state["effective_model"] = effective_model
        if self._config.runner_factory is not None:
            return self._config.runner_factory(
                model=effective_model,
                env_type=self._config.env_type,
                cwd=str(ctx.workspace),
                max_iterations=self._config.max_iterations,
                command_timeout=self._config.timeout_seconds,
                base_url=explicit_base_url,
                api_key=explicit_api_key,
            )
        if MiniSWERunner is None:  # pragma: no cover - import-guard branch
            raise RuntimeError(
                "HermesAdapter requires Hermes Agent's `mini_swe_runner.py`. "
                "Install Hermes with the official installer, or set "
                "`HERMES_AGENT_REPO=/path/to/hermes-agent` / "
                "`HERMES_MINI_SWE_RUNNER=/path/to/mini_swe_runner.py`. "
                f"Underlying import error: {_HERMES_IMPORT_ERROR!r}"
            )
        runner = MiniSWERunner(
            model=effective_model,
            env_type=self._config.env_type,
            cwd=str(ctx.workspace),
            max_iterations=self._config.max_iterations,
            command_timeout=self._config.timeout_seconds,
            base_url=explicit_base_url,
            api_key=explicit_api_key,
        )
        if self._config.provider:
            try:
                from agent.auxiliary_client import resolve_provider_client
            except Exception as exc:  # pragma: no cover - optional Hermes internals
                raise RuntimeError(
                    f"Hermes provider routing requested for '{self._config.provider}', "
                    "but Hermes provider utilities could not be imported."
                ) from exc
            client, resolved_model = resolve_provider_client(
                self._config.provider,
                model=ctx.model,
            )
            if client is None or not resolved_model:
                raise RuntimeError(
                    f"Hermes provider '{self._config.provider}' did not resolve credentials."
                )
            if self._config.provider == "openai-codex":
                client = _CodexToolMessageCompatClient(client)
            runner.client = client
            runner.model = str(resolved_model)
        return runner

    def _build_ai_agent(self, ctx: AdapterContext) -> Any:
        self._prepare_process_env(ctx)
        explicit_api_key = None if self._config.provider else self._config.api_key
        explicit_base_url = None if self._config.provider else self._config.base_url
        enabled_toolsets = self._config.enabled_toolsets or ["hermes-api-server"]
        effective_model = self._effective_model(ctx)
        provider_hint = self._runtime_provider_hint()
        ctx.adapter_state["effective_model"] = effective_model
        if self._config.agent_factory is not None:
            return self._config.agent_factory(
                model=effective_model,
                base_url=explicit_base_url,
                api_key=explicit_api_key,
                provider=provider_hint,
                api_mode=self._config.api_mode,
                max_iterations=self._config.max_iterations,
                enabled_toolsets=enabled_toolsets,
                disabled_toolsets=self._config.disabled_toolsets,
            )
        if AIAgent is None:  # pragma: no cover - import-guard branch
            raise RuntimeError(
                "HermesAdapter full mode requires Hermes Agent's `run_agent.py`. "
                "Set `HERMES_AGENT_REPO=/path/to/hermes-agent` or install Hermes. "
                f"Underlying import error: {_HERMES_AGENT_IMPORT_ERROR!r}"
            )
        return AIAgent(
            base_url=explicit_base_url,
            api_key=explicit_api_key,
            provider=provider_hint,
            api_mode=self._config.api_mode,
            model=effective_model,
            max_iterations=self._config.max_iterations,
            tool_delay=self._config.tool_delay_seconds,
            enabled_toolsets=enabled_toolsets,
            disabled_toolsets=self._config.disabled_toolsets,
            quiet_mode=True,
            verbose_logging=False,
            skip_context_files=True,
            session_id=f"clawbench-{ctx.task.id}-run{ctx.run_index}",
            platform="cli",
        )

    async def run_phase(
        self,
        phase: CanonicalPhase,
        ctx: AdapterContext,
    ) -> PhaseResult:
        """Render the phase's first user turn, invoke Hermes, parse output.

        v1 limitation: only the first turn of each phase is delivered.
        Tasks that declare `MULTI_TURN_INJECTION` as a required
        capability are filtered out at harness level before the adapter
        is invoked (harness gating lands in a later step). Guarding
        here too keeps the adapter honest if it is driven directly.
        """

        if self._config.driver_mode == "ai_agent":
            return await self._run_ai_agent_phase(phase, ctx)

        runner = ctx.adapter_state.get("runner")
        if runner is None:
            return PhaseResult(
                error="HermesAdapter.run_phase called before setup(); no runner",
                completed_normally=False,
            )

        if not phase.user.turns:
            return PhaseResult(completed_normally=True)

        # Hermes cannot receive dynamic follow-ups; we render and send
        # only the first turn. Later turns remain in the canonical
        # phase description but are intentionally dropped here.
        first_turn = phase.user.turns[0]
        message = first_turn.variant_messages.get(
            self._config.prompt_variant, first_turn.message
        )
        prompt = self._with_workspace_guidance(
            render_template(message, ctx.runtime_values),
            ctx,
        )

        phase_timeout = float(
            phase.timeout_seconds
            or ctx.task.budgets.timeout_seconds
            or self._config.timeout_seconds * self._config.max_iterations
        )

        try:
            result: dict[str, Any] = await asyncio.wait_for(
                asyncio.to_thread(runner.run_task, prompt),
                timeout=phase_timeout,
            )
        except asyncio.TimeoutError:
            return PhaseResult(
                error=f"Hermes phase '{phase.name}' exceeded {phase_timeout:.0f}s",
                completed_normally=False,
            )
        except Exception as exc:  # pragma: no cover - runner-internal error
            return PhaseResult(
                error=f"HermesAdapter runner error: {exc}",
                completed_normally=False,
            )

        phase_transcript = parse_conversation(result or {})
        ctx.transcript.messages.extend(phase_transcript.messages)

        api_calls = int(result.get("api_calls", 0)) if isinstance(result, dict) else 0
        ctx.adapter_state["api_calls"] = (
            int(ctx.adapter_state.get("api_calls", 0)) + api_calls
        )

        return PhaseResult(
            messages=phase_transcript.messages,
            adapter_metadata={
                "api_calls": api_calls,
                "hermes_metadata": result.get("metadata", {}) if isinstance(result, dict) else {},
            },
            completed_normally=bool(result.get("completed", False)) if isinstance(result, dict) else False,
        )

    def _with_workspace_guidance(self, prompt: str, ctx: AdapterContext) -> str:
        return (
            "You are running inside a ClawBench task workspace.\n"
            f"Current workspace: {ctx.workspace}\n"
            "Treat this directory as the complete task environment. "
            "Inspect files in this directory first, use relative paths for task files, "
            "and do not search outside the workspace unless the task explicitly asks you to.\n"
            "Write all created or modified artifacts inside this workspace.\n\n"
            "User task:\n"
            f"{prompt}"
        )

    async def _run_ai_agent_phase(
        self,
        phase: CanonicalPhase,
        ctx: AdapterContext,
    ) -> PhaseResult:
        agent = ctx.adapter_state.get("agent")
        if agent is None:
            return PhaseResult(
                error="HermesAdapter.run_phase called before setup(); no AIAgent",
                completed_normally=False,
            )

        simulator = UserSimulator(
            phase.user,
            ctx.runtime_values,
            prompt_variant=self._config.prompt_variant,
        )
        phase_timeout = float(
            phase.timeout_seconds
            or ctx.task.budgets.timeout_seconds
            or self._config.timeout_seconds * self._config.max_iterations
        )
        appended_messages: list = []
        phase_api_calls = 0
        completed = True

        while not simulator.is_done:
            user_message = await simulator.next_message(ctx.transcript)
            if user_message is None:
                break
            history = list(ctx.adapter_state.get("conversation_history") or [])
            try:
                result: dict[str, Any] = await asyncio.wait_for(
                    asyncio.to_thread(
                        agent.run_conversation,
                        user_message,
                        conversation_history=history or None,
                        task_id=f"{ctx.task.id}-run{ctx.run_index}",
                    ),
                    timeout=phase_timeout,
                )
            except asyncio.TimeoutError:
                return PhaseResult(
                    messages=appended_messages,
                    error=f"Hermes AIAgent phase '{phase.name}' exceeded {phase_timeout:.0f}s",
                    completed_normally=False,
                )
            except Exception as exc:  # pragma: no cover - agent-internal error
                return PhaseResult(
                    messages=appended_messages,
                    error=f"HermesAdapter AIAgent error: {exc}",
                    completed_normally=False,
                )

            messages = result.get("messages", []) if isinstance(result, dict) else []
            if not isinstance(messages, list):
                messages = []
            delta = messages[len(history):] if len(messages) >= len(history) else messages
            phase_transcript = parse_chat_messages(delta)
            ctx.transcript.messages.extend(phase_transcript.messages)
            appended_messages.extend(phase_transcript.messages)
            ctx.adapter_state["conversation_history"] = messages
            phase_api_calls += int(result.get("api_calls", 0)) if isinstance(result, dict) else 0
            completed = completed and bool(result.get("completed", False))

        ctx.adapter_state["api_calls"] = (
            int(ctx.adapter_state.get("api_calls", 0)) + phase_api_calls
        )
        return PhaseResult(
            messages=appended_messages,
            adapter_metadata={
                "api_calls": phase_api_calls,
                "driver_mode": "ai_agent",
            },
            completed_normally=completed,
        )

    async def verify_state_query(
        self,
        query: StateQuery,
        ctx: AdapterContext,
    ) -> StateQueryResult:
        if query.kind == "memory":
            fallback_state = MemoryState(
                key_pattern=str(query.selector.get("key_pattern", "")),
                exists=query.predicate != "absent",
                value_contains=list(query.expected.get("value_contains", [])),
            )
            extra_memory_text = self._read_hermes_memory_text(ctx)
            ok, detail = verify_memory_fallback(
                fallback_state,
                ctx.workspace,
                transcript=ctx.transcript,
                extra_memory_text=extra_memory_text,
            )
            return StateQueryResult(ok=ok, detail=detail)

        if self._config.driver_mode == "ai_agent" and query.kind == "session":
            expected_model = str(query.expected.get("model") or "")
            if query.predicate == "absent":
                return StateQueryResult(ok=False, detail="Hermes AIAgent session exists")
            if expected_model and expected_model.lower() not in ctx.model.lower():
                return StateQueryResult(
                    ok=False,
                    detail=f"Model mismatch: expected {expected_model}, got {ctx.model}",
                )
            return StateQueryResult(ok=True, detail="OK")

        if self._config.driver_mode == "ai_agent" and query.kind == "cron":
            return self._verify_cron_file(query, ctx)

        # HermesAdapter does not currently expose session/cron/custom
        # gateway state. Flag as capability-missing so the scorer can
        # apply the neutral skip policy.
        return StateQueryResult(
            ok=False,
            detail=(
                f"HermesAdapter does not resolve '{query.kind}' state queries "
                f"(missing capability {query.required_capability.value})"
            ),
            capability_missing=True,
        )

    def _read_hermes_memory_text(self, ctx: AdapterContext) -> str:
        hermes_home = Path(ctx.adapter_state.get("hermes_home") or self._hermes_home(ctx))
        candidates = [
            hermes_home / "memory",
            hermes_home / "memories",
            hermes_home / "user_memory",
        ]
        chunks: list[str] = []
        for candidate in candidates:
            if candidate.is_file():
                chunks.append(candidate.read_text(encoding="utf-8", errors="replace"))
            elif candidate.is_dir():
                for path in candidate.rglob("*"):
                    if path.is_file() and path.suffix.lower() in {".md", ".txt", ".json"}:
                        try:
                            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
                        except Exception:
                            continue
        return "\n".join(chunks)

    def _verify_cron_file(
        self,
        query: StateQuery,
        ctx: AdapterContext,
    ) -> StateQueryResult:
        hermes_home = Path(ctx.adapter_state.get("hermes_home") or self._hermes_home(ctx))
        jobs_file = hermes_home / "cron" / "jobs.json"
        if not jobs_file.is_file():
            if query.predicate == "absent":
                return StateQueryResult(ok=True, detail="Correctly absent")
            return StateQueryResult(ok=False, detail=f"No Hermes cron jobs file at {jobs_file}")
        try:
            payload = json.loads(jobs_file.read_text(encoding="utf-8"))
        except Exception as exc:
            return StateQueryResult(ok=False, detail=f"Could not read Hermes cron jobs: {exc}")
        jobs = payload if isinstance(payload, list) else payload.get("jobs", [])
        if not isinstance(jobs, list):
            jobs = []
        if query.predicate == "absent":
            return StateQueryResult(
                ok=not jobs,
                detail="Correctly absent" if not jobs else "Cron jobs exist",
            )
        description_contains = query.selector.get("description_contains")
        if not jobs:
            return StateQueryResult(ok=False, detail="No cron jobs found")
        if description_contains:
            needle = str(description_contains).lower()
            if not any(needle in json.dumps(job, sort_keys=True).lower() for job in jobs):
                return StateQueryResult(
                    ok=False,
                    detail=f"No cron job matched '{description_contains}'",
                )
        return StateQueryResult(ok=True, detail="OK")

    async def teardown(self, ctx: AdapterContext) -> None:
        """Release the runner reference so GC can reclaim its process pool."""

        ctx.adapter_state.pop("runner", None)
        ctx.adapter_state.pop("agent", None)


__all__ = ["HermesAdapter", "HermesAdapterConfig"]
