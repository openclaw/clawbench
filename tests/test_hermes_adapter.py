"""Tests for `HermesAdapter` against a stub `MiniSWERunner`.

We don't pull in the real `hermes-agent` package — the adapter is
driven through its `runner_factory` hook, which lets tests plug in a
fixed conversation without any network / subprocess activity.

What's covered:
- The adapter registers under the `"hermes"` name.
- `capabilities` is the minimal `{FILES, EXECUTION}` set.
- `setup` realises memory seed entries as workspace files.
- `run_phase` renders the user turn, calls the stub runner, and
  appends the parsed conversation into the shared transcript.
- `verify_state_query` falls back to workspace memory scanning for
  memory queries, and returns `capability_missing=True` for other
  kinds.
- Task gating: a task that requires MEMORY / SESSION / CRON is NOT
  supported by HermesAdapter; a files-only task is.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from clawbench.adapters import get_adapter
from clawbench.adapters.base import AdapterContext, StateQueryResult
from clawbench.adapters.hermes import HermesAdapter, HermesAdapterConfig
from clawbench.canonical import (
    AdapterCapability,
    CanonicalTask,
    StateQuery,
)
from clawbench.canonical.convert import from_task_definition
from clawbench.schemas import (
    CompletionSpec,
    ExecutionCheck,
    FileState,
    MemoryState,
    SimulatedUser,
    TaskDefinition,
    TaskFamily,
    TaskSetup,
    Tier,
    Transcript,
    UserTurn,
)


# ---------------------------------------------------------------------------
# Stub MiniSWERunner
# ---------------------------------------------------------------------------


class _StubRunner:
    """Pretends to be `MiniSWERunner`; returns a canned conversation."""

    def __init__(self, *, model: str, cwd: str, **_: object) -> None:
        self.model = model
        self.cwd = cwd
        self.last_prompt: str | None = None
        self.calls = 0
        self.conversation = {
            "conversations": [
                {"from": "user", "value": "placeholder — filled per-test"},
                {
                    "from": "assistant",
                    "value": (
                        "Running `ls`.\n"
                        '<tool_call>{"name":"bash","arguments":{"cmd":"ls"}}</tool_call>'
                    ),
                },
                {
                    "from": "tool",
                    "value": '<tool_response>{"stdout":"main.py"}</tool_response>',
                },
            ],
            "completed": True,
            "api_calls": 3,
            "metadata": {"model": "stub", "env_type": "local"},
        }

    def run_task(self, prompt: str) -> dict:
        self.last_prompt = prompt
        self.calls += 1
        # Swap the placeholder user value with the real prompt so the
        # conversation reflects what the adapter actually sent.
        convo = {**self.conversation}
        convo["conversations"] = [
            {"from": "user", "value": prompt}
            if entry.get("from") == "user"
            else entry
            for entry in convo["conversations"]
        ]
        return convo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _files_only_task(memory_seed: bool = False) -> CanonicalTask:
    setup = (
        TaskSetup(memory_seed=[{"key": "stack", "value": "React, Node"}])
        if memory_seed
        else TaskSetup()
    )
    return from_task_definition(
        TaskDefinition(
            id="hermes-files-only",
            name="Hermes files-only",
            tier=Tier.TIER1,
            family=TaskFamily.CODING,
            surface="coding",
            setup=setup,
            user=SimulatedUser(
                max_turns=1,
                turns=[UserTurn(message="List the workspace files.")],
            ),
            completion=CompletionSpec(
                files=[FileState(path="main.py", exists=True)],
                execution_checks=[ExecutionCheck(name="noop", command="true")],
            ),
        )
    )


def _memory_task() -> CanonicalTask:
    return from_task_definition(
        TaskDefinition(
            id="hermes-memory",
            name="Hermes memory",
            tier=Tier.TIER2,
            family=TaskFamily.MULTI_TOOL,
            surface="tools",
            setup=TaskSetup(),
            user=SimulatedUser(max_turns=1, turns=[UserTurn(message="remember stack=X")]),
            completion=CompletionSpec(
                memory=[MemoryState(key_pattern="stack", exists=True, value_contains=["React"])],
            ),
        )
    )


def _make_adapter() -> tuple[HermesAdapter, list[_StubRunner]]:
    runners: list[_StubRunner] = []

    def _factory(**kwargs):
        runner = _StubRunner(**kwargs)
        runners.append(runner)
        return runner

    adapter = HermesAdapter(
        HermesAdapterConfig(model="stub-model", runner_factory=_factory)
    )
    return adapter, runners


def _make_ctx(task: CanonicalTask, workspace: Path) -> AdapterContext:
    return AdapterContext(
        task=task,
        workspace=workspace,
        runtime_values={},
        run_index=0,
        model="stub-model",
        transcript=Transcript(),
    )


# ---------------------------------------------------------------------------
# Registration + capability shape
# ---------------------------------------------------------------------------


def test_hermes_adapter_is_registered() -> None:
    cls = get_adapter("hermes")
    assert cls is HermesAdapter


def test_hermes_capabilities_are_files_and_execution_only() -> None:
    assert HermesAdapter.capabilities == {
        AdapterCapability.FILES,
        AdapterCapability.EXECUTION,
    }


def test_hermes_supports_files_only_task() -> None:
    task = _files_only_task()
    assert HermesAdapter.supports(task)


def test_hermes_does_not_support_memory_task() -> None:
    task = _memory_task()
    assert not HermesAdapter.supports(task)
    missing = HermesAdapter.missing_capabilities_for(task)
    assert AdapterCapability.MEMORY in missing


def test_hermes_full_agent_capabilities_cover_memory_and_dynamic_tasks() -> None:
    task = _memory_task()
    config = HermesAdapterConfig(model="stub-model", driver_mode="ai_agent")
    assert HermesAdapter.supports(task, config)
    caps = HermesAdapter.supported_capabilities(config)
    assert AdapterCapability.MEMORY in caps
    assert AdapterCapability.CRON in caps
    assert AdapterCapability.BROWSER in caps
    assert AdapterCapability.MULTI_TURN_INJECTION in caps


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_setup_realizes_memory_seed_as_workspace_files(tmp_path: Path) -> None:
    task = _files_only_task(memory_seed=True)
    adapter, _ = _make_adapter()

    async def _go() -> None:
        async with adapter:
            ctx = _make_ctx(task, tmp_path)
            await adapter.setup(ctx)

    asyncio.run(_go())
    seeded = tmp_path / "memory" / "stack.md"
    assert seeded.is_file()
    assert "React" in seeded.read_text(encoding="utf-8")


def test_run_phase_sends_rendered_prompt_and_parses_conversation(tmp_path: Path) -> None:
    task = _files_only_task()
    adapter, runners = _make_adapter()

    async def _go():
        async with adapter:
            ctx = _make_ctx(task, tmp_path)
            await adapter.setup(ctx)
            result = await adapter.run_phase(task.phases[0], ctx)
            return ctx, result

    ctx, result = asyncio.run(_go())

    # The stub runner saw the rendered user message with workspace guidance.
    assert runners
    assert runners[0].last_prompt is not None
    assert "List the workspace files." in runners[0].last_prompt
    assert str(tmp_path) in runners[0].last_prompt
    assert "Inspect files in this directory first" in runners[0].last_prompt
    assert "do not search outside the workspace" in runners[0].last_prompt

    # Conversation parsed into the shared transcript.
    assert result.error is None
    assert ctx.transcript.tool_call_sequence, "expected tool calls parsed out of Hermes conversation"
    first_call = ctx.transcript.tool_call_sequence[0]
    assert first_call.name == "bash"
    assert first_call.input == {"cmd": "ls"}
    assert "main.py" in first_call.output
    assert result.adapter_metadata.get("api_calls") == 3
    assert result.completed_normally is True


def test_runner_factory_uses_explicit_provider_instead_of_api_key(tmp_path: Path) -> None:
    task = _files_only_task()
    calls: list[dict] = []

    def _factory(**kwargs):
        calls.append(kwargs)
        return _StubRunner(model=kwargs["model"], cwd=kwargs["cwd"])

    adapter = HermesAdapter(
        HermesAdapterConfig(
            model="stub-model",
            provider="openai-codex",
            base_url="https://example.invalid/v1",
            api_key="secret",
            runner_factory=_factory,
        )
    )

    async def _go() -> None:
        async with adapter:
            ctx = _make_ctx(task, tmp_path)
            await adapter.setup(ctx)

    asyncio.run(_go())

    assert calls
    assert calls[0]["base_url"] is None
    assert calls[0]["api_key"] is None


def test_direct_openai_endpoint_strips_provider_prefix_for_hermes(tmp_path: Path) -> None:
    task = _files_only_task()
    calls: list[dict] = []

    def _factory(**kwargs):
        calls.append(kwargs)
        return _StubRunner(model=kwargs["model"], cwd=kwargs["cwd"])

    adapter = HermesAdapter(
        HermesAdapterConfig(
            model="openai/gpt-5.4",
            base_url="https://api.openai.com/v1",
            api_key="secret",
            runner_factory=_factory,
        )
    )

    async def _go() -> None:
        async with adapter:
            ctx = AdapterContext(
                task=task,
                workspace=tmp_path,
                runtime_values={},
                run_index=0,
                model="openai/gpt-5.4",
                transcript=Transcript(),
            )
            await adapter.setup(ctx)
            assert ctx.adapter_state["effective_model"] == "gpt-5.4"

    asyncio.run(_go())

    assert calls
    assert calls[0]["model"] == "gpt-5.4"


def test_ai_agent_direct_endpoint_reports_custom_provider(tmp_path: Path) -> None:
    task = _files_only_task()
    calls: list[dict] = []

    class _StubAgent:
        pass

    def _factory(**kwargs):
        calls.append(kwargs)
        return _StubAgent()

    adapter = HermesAdapter(
        HermesAdapterConfig(
            model="openai/gpt-5.4",
            base_url="https://api.openai.com/v1",
            api_key="secret",
            driver_mode="ai_agent",
            agent_factory=_factory,
        )
    )

    async def _go() -> None:
        async with adapter:
            ctx = AdapterContext(
                task=task,
                workspace=tmp_path,
                runtime_values={},
                run_index=0,
                model="openai/gpt-5.4",
                transcript=Transcript(),
            )
            await adapter.setup(ctx)
            assert ctx.adapter_state["effective_model"] == "gpt-5.4"

    asyncio.run(_go())

    assert calls
    assert calls[0]["model"] == "gpt-5.4"
    assert calls[0]["base_url"] == "https://api.openai.com/v1"
    assert calls[0]["api_key"] == "secret"
    assert calls[0]["provider"] == "custom"


def test_ai_agent_phase_sends_workspace_guidance(tmp_path: Path) -> None:
    task = _files_only_task()
    calls: list[dict] = []

    class _StubAgent:
        def run_conversation(
            self,
            user_message: str,
            *,
            conversation_history=None,
            task_id=None,
        ) -> dict:
            calls.append(
                {
                    "user_message": user_message,
                    "conversation_history": conversation_history,
                    "task_id": task_id,
                }
            )
            return {
                "messages": [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": "Done."},
                ],
                "api_calls": 1,
                "completed": True,
            }

    adapter = HermesAdapter(
        HermesAdapterConfig(
            model="stub-model",
            driver_mode="ai_agent",
            agent_factory=lambda **_: _StubAgent(),
        )
    )

    async def _go():
        async with adapter:
            ctx = _make_ctx(task, tmp_path)
            await adapter.setup(ctx)
            return await adapter.run_phase(task.phases[0], ctx)

    result = asyncio.run(_go())

    assert result.error is None
    assert calls
    sent = calls[0]["user_message"]
    assert "List the workspace files." in sent
    assert str(tmp_path) in sent
    assert "Inspect files in this directory first" in sent
    assert "do not search outside the workspace" in sent


# ---------------------------------------------------------------------------
# State queries
# ---------------------------------------------------------------------------


def test_memory_query_uses_workspace_fallback(tmp_path: Path) -> None:
    task = _memory_task()
    adapter, _ = _make_adapter()
    # Simulate a prior run that wrote a MEMORY.md into the workspace.
    (tmp_path / "MEMORY.md").write_text("stack: React, Node, Postgres", encoding="utf-8")

    query = StateQuery(
        kind="memory",
        predicate="exists",
        selector={"key_pattern": "stack"},
        expected={"value_contains": ["React"]},
        required_capability=AdapterCapability.MEMORY,
    )

    async def _go() -> StateQueryResult:
        async with adapter:
            ctx = _make_ctx(task, tmp_path)
            await adapter.setup(ctx)
            return await adapter.verify_state_query(query, ctx)

    result = asyncio.run(_go())
    assert result.ok is True
    assert result.capability_missing is False


def test_session_query_is_reported_as_capability_missing(tmp_path: Path) -> None:
    task = _memory_task()
    adapter, _ = _make_adapter()

    query = StateQuery(
        kind="session",
        predicate="exists",
        selector={},
        expected={},
        required_capability=AdapterCapability.SESSION,
    )

    async def _go() -> StateQueryResult:
        async with adapter:
            ctx = _make_ctx(task, tmp_path)
            await adapter.setup(ctx)
            return await adapter.verify_state_query(query, ctx)

    result = asyncio.run(_go())
    assert result.capability_missing is True
    assert result.ok is False


# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------


def test_run_phase_surfaces_runner_timeout(tmp_path: Path) -> None:
    task = _files_only_task()

    class _SlowRunner:
        def __init__(self, **_: object) -> None:
            pass

        def run_task(self, prompt: str) -> dict:
            import time

            time.sleep(5)  # will exceed the test's configured timeout
            return {"conversations": [], "completed": False, "api_calls": 0}

    adapter = HermesAdapter(
        HermesAdapterConfig(
            model="stub-model",
            runner_factory=lambda **kw: _SlowRunner(**kw),
        )
    )

    # Force a short phase timeout so the test stays fast.
    task_with_short_timeout = task.model_copy(
        update={
            "phases": [
                task.phases[0].model_copy(update={"timeout_seconds": 1})
            ]
        }
    )

    async def _go():
        async with adapter:
            ctx = _make_ctx(task_with_short_timeout, tmp_path)
            await adapter.setup(ctx)
            return await adapter.run_phase(task_with_short_timeout.phases[0], ctx)

    result = asyncio.run(_go())
    assert result.error is not None
    assert "exceeded" in result.error
    assert result.completed_normally is False
