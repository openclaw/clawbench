"""Tests for `clawbench.canonical.convert.from_task_definition`.

Covers the three representative task shapes:

1. A files + execution-only task (tier-1 bugfix) — must produce
   `required_adapter_capabilities == {FILES, EXECUTION}`.
2. A memory-using, multi-phase task (tier-2 memory roundtrip) — must
   include `MEMORY` and MULTI_TURN_INJECTION is NOT set since each
   phase's user has exactly one static turn.
3. A synthetic task exercising gateway_assertions, session, cron, and
   browser — must surface each capability.

The tests also round-trip the real task corpus through the converter
to make sure every live YAML file produces a valid `CanonicalTask`
(no missing-field or validation errors), since the converter is how
every downstream adapter will see tasks.
"""

from __future__ import annotations

from clawbench.canonical import (
    AdapterCapability,
    CanonicalTask,
    from_task_definition,
)
from clawbench.schemas import (
    BackgroundService,
    CompletionSpec,
    CronState,
    ExecutionCheck,
    FileState,
    GatewayAssertion,
    MemoryState,
    SessionState,
    SimulatedUser,
    TaskDefinition,
    TaskFamily,
    TaskSetup,
    Tier,
    UserTurn,
)
from clawbench.tasks import load_all_tasks


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _files_only_task() -> TaskDefinition:
    return TaskDefinition(
        id="test-files-only",
        name="Files-only task",
        tier=Tier.TIER1,
        family=TaskFamily.CODING,
        surface="coding",
        setup=TaskSetup(asset_packs=["pack_a"]),
        user=SimulatedUser(
            max_turns=2,
            turns=[UserTurn(message="Fix the bug and run the tests.")],
        ),
        completion=CompletionSpec(
            files=[FileState(path="src/main.py", exists=True)],
            execution_checks=[ExecutionCheck(name="tests", command="pytest -q")],
        ),
    )


def _memory_task() -> TaskDefinition:
    return TaskDefinition(
        id="test-memory-roundtrip",
        name="Memory roundtrip",
        tier=Tier.TIER2,
        family=TaskFamily.MULTI_TOOL,
        surface="tools",
        setup=TaskSetup(
            memory_seed=[{"key": "existing_key", "value": "existing_value"}],
        ),
        phases=[
            {
                "name": "store",
                "user": SimulatedUser(
                    max_turns=1,
                    turns=[UserTurn(message="Remember: stack = React, Node, Postgres.")],
                ),
            },
            {
                "name": "recall",
                "user": SimulatedUser(
                    max_turns=1,
                    turns=[UserTurn(message="What's my stack?")],
                ),
            },
        ],
        completion=CompletionSpec(
            memory=[MemoryState(key_pattern="stack", exists=True, value_contains=["React"])],
        ),
    )


def _full_surface_task() -> TaskDefinition:
    # Synthetic task exercising session, cron, gateway_assertion, browser,
    # and a dynamic follow-up turn.
    return TaskDefinition(
        id="test-full-surface",
        name="Full surface",
        tier=Tier.TIER3,
        family=TaskFamily.BROWSER,
        surface="browser",
        setup=TaskSetup(
            pre_check_gateway=[
                GatewayAssertion(
                    method="agents.list",
                    assert_path="$.count",
                    assert_equals=0,
                ),
            ],
            background_services=[
                BackgroundService(
                    name="echo-service",
                    command="python3 -m http.server",
                    port=0,
                    ready_path="/",
                ),
            ],
        ),
        user=SimulatedUser(
            max_turns=4,
            turns=[
                UserTurn(message="Start the task."),
                UserTurn(
                    message="Try again.",
                    when_tool_family="browser",
                    when_last_tool_failed=True,
                ),
            ],
        ),
        completion=CompletionSpec(
            session=SessionState(should_exist=True, model_should_be="claude-opus-4"),
            cron=[CronState(exists=True, description_contains="daily")],
            gateway_assertions=[
                GatewayAssertion(
                    method="memory.list",
                    assert_path="$.count",
                    assert_equals=1,
                ),
            ],
        ),
    )


# ---------------------------------------------------------------------------
# Capability inference
# ---------------------------------------------------------------------------


def test_files_only_task_requires_only_files_and_execution() -> None:
    task = _files_only_task()
    task.category = "software_engineering"
    task.domain = "devtools"
    task.functionality = ["bugfix", "test_verification"]
    task.trace_distribution = ["read_heavy", "edit_heavy", "execute_heavy"]
    task.tool_surface = ["filesystem", "shell"]
    task.risk_tags = ["code_regression"]
    task.surfaces = ["repo", "shell"]
    task.turn_count = 2
    task.artifact_count = 1
    task.statefulness = "workspace"
    task.evidence_risk = "low"

    canonical = from_task_definition(task)
    assert isinstance(canonical, CanonicalTask)
    assert canonical.required_adapter_capabilities == {
        AdapterCapability.FILES,
        AdapterCapability.EXECUTION,
    }
    assert canonical.category == "software_engineering"
    assert canonical.domain == "devtools"
    assert canonical.functionality == ["bugfix", "test_verification"]
    assert canonical.trace_distribution == ["read_heavy", "edit_heavy", "execute_heavy"]
    assert canonical.tool_surface == ["filesystem", "shell"]
    assert canonical.risk_tags == ["code_regression"]
    assert canonical.surfaces == ["repo", "shell"]
    assert canonical.turn_count == 2
    assert canonical.artifact_count == 1
    assert canonical.statefulness == "workspace"
    assert canonical.evidence_risk == "low"
    # Seed state should carry the asset pack through.
    assert len(canonical.assets.seed_state) == 1
    assert canonical.assets.seed_state[0].kind == "file"
    assert canonical.assets.seed_state[0].asset_pack == "pack_a"
    # File + execution checks carry over.
    assert len(canonical.verifier.file_states) == 1
    assert len(canonical.verifier.execution_checks) == 1
    assert canonical.verifier.state_queries == []
    # One non-dynamic phase → no dynamic-trigger capability.
    assert canonical.interaction.uses_dynamic_user_triggers is False


def test_memory_task_requires_memory_capability() -> None:
    canonical = from_task_definition(_memory_task())
    assert AdapterCapability.MEMORY in canonical.required_adapter_capabilities
    # Two phases with a single static turn each → dynamic-trigger is NOT
    # required (the simulated user just sends one message per phase).
    assert AdapterCapability.MULTI_TURN_INJECTION not in canonical.required_adapter_capabilities
    assert canonical.interaction.allow_multi_phase is True
    assert len(canonical.phases) == 2
    # Memory seed lifted to SeedEntry.
    memory_seeds = [s for s in canonical.assets.seed_state if s.kind == "memory"]
    assert len(memory_seeds) == 1
    assert memory_seeds[0].key == "existing_key"
    # Memory completion check → StateQuery with MEMORY capability.
    memory_queries = [q for q in canonical.verifier.state_queries if q.kind == "memory"]
    assert len(memory_queries) == 1
    assert memory_queries[0].required_capability is AdapterCapability.MEMORY
    assert memory_queries[0].selector == {"key_pattern": "stack"}
    assert memory_queries[0].expected == {"value_contains": ["React"]}


def test_full_surface_task_surfaces_every_capability() -> None:
    canonical = from_task_definition(_full_surface_task())
    caps = canonical.required_adapter_capabilities
    assert AdapterCapability.FILES in caps
    assert AdapterCapability.EXECUTION in caps
    assert AdapterCapability.SESSION in caps
    assert AdapterCapability.CRON in caps
    assert AdapterCapability.GATEWAY_RPC in caps
    assert AdapterCapability.BROWSER in caps
    # Dynamic turn (when_tool_family + when_last_tool_failed) flags MTI.
    assert AdapterCapability.MULTI_TURN_INJECTION in caps
    # pre_check_gateway survives as a pre-run query.
    assert len(canonical.verifier.pre_run_queries) == 1
    assert canonical.verifier.pre_run_queries[0].required_capability is AdapterCapability.GATEWAY_RPC
    # gateway_assertions route through the verifier state_queries.
    gateway_queries = [
        q for q in canonical.verifier.state_queries if q.kind == "custom"
    ]
    assert len(gateway_queries) == 1
    assert gateway_queries[0].selector["method"] == "memory.list"
    # Session state with model constraint surfaces in expected.
    session_queries = [q for q in canonical.verifier.state_queries if q.kind == "session"]
    assert len(session_queries) == 1
    assert session_queries[0].expected == {"model": "claude-opus-4"}


def test_background_services_pass_through_unchanged() -> None:
    canonical = from_task_definition(_full_surface_task())
    assert len(canonical.assets.background_services) == 1
    service = canonical.assets.background_services[0]
    assert service.name == "echo-service"
    assert service.command == "python3 -m http.server"


# ---------------------------------------------------------------------------
# Whole-corpus smoke
# ---------------------------------------------------------------------------


def test_every_task_in_corpus_converts() -> None:
    """Every shipped task YAML must produce a valid CanonicalTask.

    Acts as a regression gate: any new field added to TaskDefinition that
    the converter doesn't know about will likely still work (fields it
    ignores don't break canonical), but any task using new completion
    shapes that the converter can't translate will raise here.
    """
    tasks = load_all_tasks()
    assert tasks, "expected at least one task in the corpus"
    for task in tasks:
        canonical = from_task_definition(task)
        # Every canonical task must declare FILES + EXECUTION capability.
        assert AdapterCapability.FILES in canonical.required_adapter_capabilities
        assert AdapterCapability.EXECUTION in canonical.required_adapter_capabilities
        # Phases always have at least one entry (normalized_phases fills
        # one from `user` when `phases` is absent).
        assert canonical.phases, f"{task.id}: canonical phases empty"
        # Budgets honour the source timeout.
        assert canonical.budgets.timeout_seconds == task.timeout_seconds
