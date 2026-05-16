"""Convert `TaskDefinition` → `CanonicalTask`.

This is the single bridge between the existing OpenClaw-entangled task
format (`clawbench.schemas.TaskDefinition`) and the agent-agnostic
canonical form (`CanonicalTask`). Callers load tasks as usual via
`clawbench.tasks.load_all_tasks` and then call
`from_task_definition(task)` to get the canonical view.

Field mappings (any field not mentioned is copied verbatim):

- `setup.asset_packs`           → `assets.seed_state` (kind="file", asset_pack=...)
- `setup.workspace_files`       → `assets.workspace_files`
- `setup.background_services`   → `assets.background_services`
- `setup.memory_seed`           → `assets.seed_state` (kind="memory")
- `setup.pre_check_gateway`     → `verifier.pre_run_queries` (GATEWAY_RPC)
- `completion.files`            → `verifier.file_states`
- `completion.execution_checks` → `verifier.execution_checks`
- `completion.memory`           → `verifier.state_queries` (MEMORY)
- `completion.session`          → `verifier.state_queries` (SESSION)
- `completion.cron`             → `verifier.state_queries` (CRON)
- `completion.gateway_assertions` → `verifier.state_queries` (GATEWAY_RPC)
- `trajectory`                  → `verifier.trajectory`
- `behavior`                    → `verifier.behavior`
- `judge`                       → `verifier.judge`
- `user` / `phases`             → `phases` via `task.normalized_phases()`
- `timeout_seconds`             → `budgets.timeout_seconds` (also on each phase)

`required_adapter_capabilities` is computed from what the task actually
needs: always `{FILES, EXECUTION}`, plus `MEMORY`/`SESSION`/`CRON`/
`GATEWAY_RPC`/`BROWSER`/`MULTI_TURN_INJECTION` when the source task's
fields trigger those capabilities.
"""

from __future__ import annotations

from clawbench.canonical.schema import (
    AdapterCapability,
    BudgetSpec,
    CanonicalAssets,
    CanonicalPhase,
    CanonicalTask,
    InteractionPolicy,
    SeedEntry,
    StateQuery,
    VerifierContract,
)
from clawbench.schemas import (
    CronState,
    GatewayAssertion,
    MemoryState,
    SessionState,
    TaskDefinition,
    TaskFamily,
    UserTurn,
)


# ---------------------------------------------------------------------------
# Seed state
# ---------------------------------------------------------------------------


def _seeds_from_setup(task: TaskDefinition) -> list[SeedEntry]:
    seeds: list[SeedEntry] = []
    for pack in task.setup.asset_packs:
        seeds.append(SeedEntry(kind="file", asset_pack=pack))
    for entry in task.setup.memory_seed:
        # memory_seed entries are free-form dicts in the existing schema;
        # we preserve them verbatim in `metadata` and surface `key` +
        # `content` when present so adapters can consume the structured
        # pieces without re-parsing.
        seeds.append(
            SeedEntry(
                kind="memory",
                key=str(entry.get("key", "")),
                content=entry.get("value") or entry.get("content"),
                metadata=dict(entry),
            )
        )
    return seeds


# ---------------------------------------------------------------------------
# State queries: memory / session / cron / gateway_assertions
# ---------------------------------------------------------------------------


def _memory_state_to_query(state: MemoryState) -> StateQuery:
    expected: dict[str, object] = {}
    if state.value_contains:
        expected["value_contains"] = list(state.value_contains)
    return StateQuery(
        kind="memory",
        predicate="exists" if state.exists else "absent",
        selector={"key_pattern": state.key_pattern},
        expected=expected,
        required_capability=AdapterCapability.MEMORY,
        description=f"memory key ~ /{state.key_pattern}/",
    )


def _session_state_to_query(state: SessionState) -> StateQuery:
    expected: dict[str, object] = {}
    if state.model_should_be:
        expected["model"] = state.model_should_be
    return StateQuery(
        kind="session",
        predicate="exists" if state.should_exist else "absent",
        selector={},
        expected=expected,
        required_capability=AdapterCapability.SESSION,
        description="session state",
    )


def _cron_state_to_query(state: CronState) -> StateQuery:
    selector: dict[str, object] = {}
    if state.description_contains:
        selector["description_contains"] = state.description_contains
    return StateQuery(
        kind="cron",
        predicate="exists" if state.exists else "absent",
        selector=selector,
        expected={},
        required_capability=AdapterCapability.CRON,
        description="cron schedule",
    )


def _gateway_assertion_to_query(assertion: GatewayAssertion) -> StateQuery:
    selector: dict[str, object] = {
        "method": assertion.method,
        "params": dict(assertion.params),
        "assert_path": assertion.assert_path,
    }
    expected: dict[str, object] = {}
    if assertion.assert_equals is not None:
        expected["equals"] = assertion.assert_equals
    if assertion.assert_contains is not None:
        expected["contains"] = assertion.assert_contains
    expected["exists"] = assertion.assert_exists
    predicate = "exists"
    if assertion.assert_equals is not None:
        predicate = "equals"
    elif assertion.assert_contains is not None:
        predicate = "contains"
    elif not assertion.assert_exists:
        predicate = "absent"
    return StateQuery(
        kind="custom",
        predicate=predicate,
        selector=selector,
        expected=expected,
        required_capability=AdapterCapability.GATEWAY_RPC,
        description=f"gateway rpc: {assertion.method}",
    )


def _state_queries_from_completion(task: TaskDefinition) -> list[StateQuery]:
    queries: list[StateQuery] = []
    for mem in task.completion.memory:
        queries.append(_memory_state_to_query(mem))
    if task.completion.session is not None:
        queries.append(_session_state_to_query(task.completion.session))
    for cron in task.completion.cron:
        queries.append(_cron_state_to_query(cron))
    for assertion in task.completion.gateway_assertions:
        queries.append(_gateway_assertion_to_query(assertion))
    return queries


def _pre_run_queries_from_setup(task: TaskDefinition) -> list[StateQuery]:
    return [_gateway_assertion_to_query(a) for a in task.setup.pre_check_gateway]


# ---------------------------------------------------------------------------
# Phases + dynamic-turn detection
# ---------------------------------------------------------------------------


_DYNAMIC_TURN_FIELDS = (
    "when_tool_family",
    "when_tool_name",
    "when_assistant_contains",
    "when_last_tool_failed",
)


def _turn_is_dynamic(turn: UserTurn) -> bool:
    if turn.when_last_tool_failed:
        return True
    for name in _DYNAMIC_TURN_FIELDS:
        value = getattr(turn, name, None)
        if isinstance(value, bool):
            if value:
                return True
        elif value:
            return True
    return False


def _phases_from_task(task: TaskDefinition) -> tuple[list[CanonicalPhase], bool]:
    phases: list[CanonicalPhase] = []
    any_dynamic = False
    for phase in task.normalized_phases():
        phases.append(
            CanonicalPhase(
                name=phase.name,
                user=phase.user,
                timeout_seconds=phase.timeout_seconds,
            )
        )
        if len(phase.user.turns) > 1 or any(_turn_is_dynamic(t) for t in phase.user.turns):
            any_dynamic = True
    return phases, any_dynamic


# ---------------------------------------------------------------------------
# Capability inference
# ---------------------------------------------------------------------------


def _capabilities_for_task(task: TaskDefinition, *, uses_dynamic: bool) -> set[AdapterCapability]:
    caps: set[AdapterCapability] = {AdapterCapability.FILES, AdapterCapability.EXECUTION}
    if task.completion.memory or any(seed.get("key") for seed in task.setup.memory_seed):
        caps.add(AdapterCapability.MEMORY)
    if task.completion.session is not None:
        caps.add(AdapterCapability.SESSION)
    if task.completion.cron:
        caps.add(AdapterCapability.CRON)
    if task.completion.gateway_assertions or task.setup.pre_check_gateway:
        caps.add(AdapterCapability.GATEWAY_RPC)
    if task.family == TaskFamily.BROWSER:
        caps.add(AdapterCapability.BROWSER)
    if uses_dynamic:
        caps.add(AdapterCapability.MULTI_TURN_INJECTION)
    return caps


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def from_task_definition(task: TaskDefinition) -> CanonicalTask:
    """Produce the canonical view of a legacy `TaskDefinition`.

    This is lossless for fields that have a canonical equivalent.
    OpenClaw-only constructs (gateway_assertions, pre_check_gateway,
    memory_seed) become `StateQuery` entries / `SeedEntry` entries
    tagged with the capability an adapter needs to resolve them.
    """

    phases, any_dynamic = _phases_from_task(task)

    assets = CanonicalAssets(
        workspace_files=list(task.setup.workspace_files),
        background_services=list(task.setup.background_services),
        seed_state=_seeds_from_setup(task),
    )

    verifier = VerifierContract(
        file_states=list(task.completion.files),
        execution_checks=list(task.completion.execution_checks),
        state_queries=_state_queries_from_completion(task),
        pre_run_queries=_pre_run_queries_from_setup(task),
        trajectory=task.trajectory,
        behavior=task.behavior,
        judge=task.judge,
    )

    interaction = InteractionPolicy(
        max_turns=max((phase.user.max_turns for phase in phases), default=20),
        allow_multi_phase=len(phases) > 1,
        uses_dynamic_user_triggers=any_dynamic,
    )

    budgets = BudgetSpec(timeout_seconds=task.timeout_seconds)

    capabilities = _capabilities_for_task(task, uses_dynamic=any_dynamic)

    return CanonicalTask(
        id=task.id,
        name=task.name,
        tier=task.tier,
        family=task.family,
        surface=task.surface,
        scenario=task.scenario,
        subscenario=task.subscenario,
        capabilities=list(task.capabilities),
        atomic_capabilities=list(task.atomic_capabilities),
        pool=task.pool,
        subsets=list(task.subsets),
        variant_group=task.variant_group,
        variant_id=task.variant_id,
        template_id=task.template_id,
        release_id=task.release_id,
        source_kind=task.source_kind,
        provenance_ids=list(task.provenance_ids),
        privacy_tier=task.privacy_tier,
        contamination_risk=task.contamination_risk,
        freshness_epoch=task.freshness_epoch,
        category=task.category,
        domain=task.domain,
        functionality=list(task.functionality),
        trace_distribution=list(task.trace_distribution),
        tool_surface=list(task.tool_surface),
        risk_tags=list(task.risk_tags),
        surfaces=list(task.surfaces),
        turn_count=task.turn_count,
        artifact_count=task.artifact_count,
        statefulness=task.statefulness,
        evidence_risk=task.evidence_risk,
        first_used_at=task.first_used_at,
        retire_after_runs=task.retire_after_runs,
        similarity_hash=task.similarity_hash,
        canary_token=task.canary_token,
        official=task.official,
        query_difficulty=task.query_difficulty,
        query_weight=task.query_weight,
        artifact_type=task.artifact_type,
        preconditions=list(task.preconditions),
        source_dataset=task.source_dataset,
        prompt_variants=list(task.prompt_variants),
        pass_threshold=task.pass_threshold,
        assets=assets,
        phases=phases,
        verifier=verifier,
        budgets=budgets,
        interaction=interaction,
        deliverables=[],
        required_adapter_capabilities=capabilities,
    )
