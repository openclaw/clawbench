"""Tests for `clawbench.adapters.base` + registry.

Keeps the adapter ABC and registration helpers honest before any
concrete adapter lands. A parametrized contract test in
`test_adapter_contract.py` will exercise the ABC against every shipped
adapter later.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clawbench.adapters import (
    ADAPTERS,
    AdapterContext,
    AgentAdapter,
    PhaseResult,
    StateQueryResult,
    get_adapter,
    register_adapter,
)
from clawbench.canonical import (
    AdapterCapability,
    CanonicalPhase,
    CanonicalTask,
    StateQuery,
)
from clawbench.canonical.convert import from_task_definition
from clawbench.schemas import (
    CompletionSpec,
    ExecutionCheck,
    FileState,
    SimulatedUser,
    TaskDefinition,
    TaskFamily,
    TaskSetup,
    Tier,
    Transcript,
    UserTurn,
)


# ---------------------------------------------------------------------------
# Minimal adapter for contract verification.
# ---------------------------------------------------------------------------


class _EchoAdapter(AgentAdapter):
    name = "echo-test-adapter"
    capabilities = {AdapterCapability.FILES, AdapterCapability.EXECUTION}

    async def setup(self, ctx: AdapterContext) -> None:  # pragma: no cover - trivial
        return None

    async def run_phase(
        self, phase: CanonicalPhase, ctx: AdapterContext
    ) -> PhaseResult:
        return PhaseResult(messages=[], adapter_metadata={"phase": phase.name})

    async def verify_state_query(
        self, query: StateQuery, ctx: AdapterContext
    ) -> StateQueryResult:
        if query.required_capability in self.capabilities:
            return StateQueryResult(ok=True, detail="echo-adapter-always-ok")
        return StateQueryResult(
            ok=False,
            detail=f"echo adapter does not provide {query.required_capability.value}",
            capability_missing=True,
        )

    async def teardown(self, ctx: AdapterContext) -> None:  # pragma: no cover - trivial
        return None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_register_adapter_adds_to_registry_and_get_adapter_resolves() -> None:
    original = dict(ADAPTERS)
    try:
        register_adapter(_EchoAdapter)
        assert ADAPTERS["echo-test-adapter"] is _EchoAdapter
        assert get_adapter("echo-test-adapter") is _EchoAdapter
    finally:
        ADAPTERS.clear()
        ADAPTERS.update(original)


def test_register_adapter_rejects_duplicate_name() -> None:
    class _OtherEcho(AgentAdapter):
        name = "echo-test-adapter"
        capabilities = {AdapterCapability.FILES}

        async def setup(self, ctx: AdapterContext) -> None:  # pragma: no cover
            return None

        async def run_phase(self, phase, ctx) -> PhaseResult:  # pragma: no cover
            return PhaseResult()

        async def verify_state_query(self, query, ctx) -> StateQueryResult:  # pragma: no cover
            return StateQueryResult(ok=False, capability_missing=True)

        async def teardown(self, ctx: AdapterContext) -> None:  # pragma: no cover
            return None

    original = dict(ADAPTERS)
    try:
        register_adapter(_EchoAdapter)
        with pytest.raises(ValueError):
            register_adapter(_OtherEcho)
    finally:
        ADAPTERS.clear()
        ADAPTERS.update(original)


def test_register_adapter_requires_name() -> None:
    class _Nameless(AgentAdapter):
        capabilities = {AdapterCapability.FILES}

        async def setup(self, ctx: AdapterContext) -> None:  # pragma: no cover
            return None

        async def run_phase(self, phase, ctx) -> PhaseResult:  # pragma: no cover
            return PhaseResult()

        async def verify_state_query(self, query, ctx) -> StateQueryResult:  # pragma: no cover
            return StateQueryResult(ok=False, capability_missing=True)

        async def teardown(self, ctx: AdapterContext) -> None:  # pragma: no cover
            return None

    with pytest.raises(ValueError):
        register_adapter(_Nameless)


def test_get_adapter_raises_for_unknown_name() -> None:
    with pytest.raises(KeyError):
        get_adapter("no-such-adapter-exists")


# ---------------------------------------------------------------------------
# Capability gating helpers
# ---------------------------------------------------------------------------


def _file_task() -> CanonicalTask:
    task = TaskDefinition(
        id="capability-test",
        name="capability test",
        tier=Tier.TIER1,
        family=TaskFamily.CODING,
        surface="coding",
        setup=TaskSetup(),
        user=SimulatedUser(
            max_turns=1, turns=[UserTurn(message="Do a thing.")]
        ),
        completion=CompletionSpec(
            files=[FileState(path="out.txt", exists=True)],
            execution_checks=[ExecutionCheck(name="ok", command="true")],
        ),
    )
    return from_task_definition(task)


def test_supports_is_true_when_capabilities_cover_task() -> None:
    task = _file_task()
    assert _EchoAdapter.supports(task)
    assert _EchoAdapter.missing_capabilities_for(task) == set()


def test_supports_is_false_when_task_needs_more() -> None:
    task = _file_task()
    task = task.model_copy(
        update={
            "required_adapter_capabilities": (
                task.required_adapter_capabilities | {AdapterCapability.MEMORY}
            )
        }
    )
    assert not _EchoAdapter.supports(task)
    assert _EchoAdapter.missing_capabilities_for(task) == {AdapterCapability.MEMORY}


# ---------------------------------------------------------------------------
# Context roundtrip (sanity: adapter methods can build and return
# PhaseResult / StateQueryResult without tripping dataclass defaults)
# ---------------------------------------------------------------------------


def test_adapter_phase_result_round_trip(tmp_path: Path) -> None:
    task = _file_task()
    adapter = _EchoAdapter()
    ctx = AdapterContext(
        task=task,
        workspace=tmp_path,
        runtime_values={},
        run_index=0,
        model="test-model",
        transcript=Transcript(),
    )

    import asyncio

    async def _go() -> None:
        await adapter.setup(ctx)
        result = await adapter.run_phase(task.phases[0], ctx)
        assert isinstance(result, PhaseResult)
        assert result.adapter_metadata == {"phase": task.phases[0].name}
        query = StateQuery(
            kind="memory",
            required_capability=AdapterCapability.MEMORY,
            selector={"key_pattern": "x"},
        )
        res = await adapter.verify_state_query(query, ctx)
        assert res.capability_missing is True
        await adapter.teardown(ctx)

    asyncio.run(_go())
