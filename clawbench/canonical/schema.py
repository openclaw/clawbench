"""Canonical task schema — agent-agnostic intent.

This is the Phase-4 split of `TaskDefinition` (see CLAWBENCH_V0_4_SPEC.md
§"Canonical Task Schema"). The canonical layer expresses **what** a task
is — its identity, prompts, assets, and verification contract — without
saying **how** it gets executed. The "how" (gateway RPCs, session
lifecycle, tool-family normalization) lives in per-adapter code under
`clawbench/adapters/`.

The rule of thumb:

- If a field describes what the user asked for, what files/state the
  agent is expected to produce, or what the run must satisfy to pass,
  it belongs here.
- If a field describes how OpenClaw's gateway is called to drive the
  run or read back state, it belongs in the OpenClaw adapter (and the
  canonical version of that check is a `StateQuery` with a
  `required_capability`).

Converting from `TaskDefinition` → `CanonicalTask` is lossless for fields
that have a canonical equivalent; OpenClaw-only fields (like
`pre_check_gateway` and `gateway_assertions`) survive as `StateQuery`
entries tagged with `AdapterCapability.GATEWAY_RPC`, so adapters that
support them can still resolve them while adapters that don't can cleanly
report a capability gap.
"""

from __future__ import annotations

import enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from clawbench.schemas import (
    ArtifactType,
    BackgroundService,
    BehaviorExpectations,
    CapabilityTag,
    ExecutionCheck,
    FileState,
    JudgeExpectations,
    PromptVariant,
    QueryDifficulty,
    ScenarioDomain,
    SimulatedUser,
    TaskFamily,
    TaskPool,
    TaskSubset,
    Tier,
    TrajectoryExpectations,
)


class AdapterCapability(str, enum.Enum):
    """What an adapter is able to provide to a running task.

    Each `StateQuery` declares a `required_capability`. If the selected
    adapter's `capabilities` set does not include that capability, the
    harness either skips the task entirely (strict mode) or scores the
    query as neutral (partial mode). This keeps the leaderboard honest
    about what an adapter can actually evaluate.
    """

    FILES = "files"
    EXECUTION = "execution"
    MEMORY = "memory"
    SESSION = "session"
    CRON = "cron"
    BROWSER = "browser"
    GATEWAY_RPC = "gateway_rpc"
    # The adapter can deliver additional user turns mid-trajectory in
    # response to simulated-user triggers (when_tool_family,
    # when_assistant_contains, etc). Single-shot drivers like Hermes's
    # MiniSWERunner do not provide this.
    MULTI_TURN_INJECTION = "multi_turn_injection"


StateQueryKind = Literal["memory", "session", "cron", "custom"]
StateQueryPredicate = Literal["exists", "absent", "equals", "contains"]


class StateQuery(BaseModel):
    """An abstract state assertion resolved by the active adapter.

    The canonical layer does not commit to how the state is read. For
    example, a `kind="memory"` query with `selector={"key_pattern":"alpha"}`
    and `expected={"value_contains":["foo"]}` means "there is a memory
    entry whose key matches /alpha/ and whose value contains 'foo'".
    OpenClaw's adapter resolves that against the `memory.search` gateway
    RPC; a filesystem-memory adapter (e.g. Hermes) resolves it by
    scanning `MEMORY.md` / `memory/notes.md` in the workspace.

    The `required_capability` is what the harness checks against the
    adapter's declared capability set.
    """

    kind: StateQueryKind
    predicate: StateQueryPredicate = "exists"
    selector: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any] = Field(default_factory=dict)
    required_capability: AdapterCapability
    description: str = ""


class SeedEntry(BaseModel):
    """A single piece of pre-task state to seed into the workspace.

    `kind="file"`: the adapter writes `content` (or copies a bundled
    asset via `asset_pack`) to `path` inside the workspace.
    `kind="memory"`: the adapter seeds a memory entry with `key` and
    `content`. Adapters without memory support fall back to writing
    the seed as a file (see `environment_files.verify_memory_fallback`).
    """

    kind: Literal["file", "memory"]
    path: str | None = None
    content: str | None = None
    key: str | None = None
    asset_pack: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_shape(self) -> SeedEntry:
        if self.kind == "file" and not self.path and not self.asset_pack:
            raise ValueError("SeedEntry(kind='file') requires `path` or `asset_pack`.")
        if self.kind == "memory" and not self.key:
            raise ValueError("SeedEntry(kind='memory') requires `key`.")
        return self


class Deliverable(BaseModel):
    """A user-visible artifact the task is expected to produce."""

    kind: ArtifactType
    paths: list[str] = Field(default_factory=list)
    description: str = ""


class BudgetSpec(BaseModel):
    """Per-task execution budgets.

    `timeout_seconds` is the wall clock for the full run (all phases).
    `max_tool_calls=0` means unbounded within the timeout. Adapters are
    expected to honor these as soft caps; the harness will also enforce
    the timeout as a hard deadline.
    """

    timeout_seconds: int = 180
    max_tool_calls: int = 0
    per_turn_timeout_seconds: int = 0


class InteractionPolicy(BaseModel):
    """How the canonical phases drive the agent."""

    max_turns: int = 20
    allow_multi_phase: bool = True
    # Declares that the task's simulated user sends follow-up turns
    # based on trajectory triggers (not just counts). Adapters without
    # MULTI_TURN_INJECTION cannot deliver these dynamically.
    uses_dynamic_user_triggers: bool = False


class VerifierContract(BaseModel):
    """Everything needed to score a run, independent of how it ran.

    The file/execution halves are fully agent-agnostic — `environment_files`
    evaluates them against the workspace directly. State queries are
    resolved by `adapter.verify_state_query`. Trajectory and behavior
    expectations are evaluated against the `Transcript` (already agent-
    agnostic). The optional judge rubric is evaluated against artifacts
    + transcript + completion feedback.
    """

    file_states: list[FileState] = Field(default_factory=list)
    execution_checks: list[ExecutionCheck] = Field(default_factory=list)
    state_queries: list[StateQuery] = Field(default_factory=list)
    pre_run_queries: list[StateQuery] = Field(default_factory=list)
    trajectory: TrajectoryExpectations = Field(default_factory=TrajectoryExpectations)
    behavior: BehaviorExpectations = Field(default_factory=BehaviorExpectations)
    judge: JudgeExpectations | None = None


class CanonicalAssets(BaseModel):
    """Workspace + seed state the harness realizes before phases run.

    `workspace_files` is a list of relative paths (resolved against the
    task's assets/ dir) to copy into the workspace. `background_services`
    is already canonical (subprocess + readiness probe, no OpenClaw
    coupling). `seed_state` replaces `asset_packs` + `memory_seed` with
    a uniform per-entry list.
    """

    workspace_files: list[str] = Field(default_factory=list)
    background_services: list[BackgroundService] = Field(default_factory=list)
    seed_state: list[SeedEntry] = Field(default_factory=list)


class CanonicalPhase(BaseModel):
    """One simulated-user phase in a multi-phase task.

    `user` is reused verbatim from `clawbench.schemas.SimulatedUser` —
    it is already agent-agnostic (turn text + canonical trigger
    predicates). Whether a specific trigger fires on a given adapter
    depends on whether tool-family tags are populated, which is an
    adapter responsibility.
    """

    name: str
    user: SimulatedUser
    timeout_seconds: int | None = None


class CanonicalTask(BaseModel):
    """Agent-agnostic task definition.

    Produced by `convert.from_task_definition` from an existing
    `TaskDefinition`. Consumed by adapters via `AdapterContext` and by
    the scorer + trajectory/judge layers. No field here is OpenClaw-
    specific; OpenClaw-only semantics survive as `StateQuery` entries
    with `required_capability=GATEWAY_RPC`.
    """

    # Identity and taxonomy (already canonical in TaskDefinition).
    id: str
    name: str
    tier: Tier
    family: TaskFamily
    surface: str
    scenario: ScenarioDomain | None = None
    subscenario: str = ""
    capabilities: list[CapabilityTag] = Field(default_factory=list)
    atomic_capabilities: list[str] = Field(default_factory=list)

    # Pool / rotation / provenance.
    pool: TaskPool = TaskPool.PUBLIC_DEV
    subsets: list[TaskSubset] = Field(default_factory=list)
    variant_group: str = ""
    variant_id: str = "main"
    template_id: str = ""
    release_id: str = ""
    source_kind: str = ""
    provenance_ids: list[str] = Field(default_factory=list)
    privacy_tier: str = ""
    contamination_risk: str = ""
    freshness_epoch: str = ""
    category: str = ""
    domain: str = ""
    functionality: list[str] = Field(default_factory=list)
    trace_distribution: list[str] = Field(default_factory=list)
    tool_surface: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    surfaces: list[str] = Field(default_factory=list)
    turn_count: int = 0
    artifact_count: int = 0
    statefulness: str = ""
    evidence_risk: str = ""
    first_used_at: str = ""
    retire_after_runs: int = 0
    similarity_hash: str = ""
    canary_token: str = ""
    official: bool = False

    # Policy + prompts.
    query_difficulty: QueryDifficulty | None = None
    query_weight: float = 1.0
    artifact_type: ArtifactType | None = None
    preconditions: list[str] = Field(default_factory=list)
    source_dataset: str = ""
    prompt_variants: list[PromptVariant] = Field(default_factory=lambda: [PromptVariant.CLEAR])
    pass_threshold: float = 0.7

    # Canonical body.
    assets: CanonicalAssets = Field(default_factory=CanonicalAssets)
    phases: list[CanonicalPhase]
    verifier: VerifierContract = Field(default_factory=VerifierContract)
    budgets: BudgetSpec = Field(default_factory=BudgetSpec)
    interaction: InteractionPolicy = Field(default_factory=InteractionPolicy)
    deliverables: list[Deliverable] = Field(default_factory=list)

    # Adapter gating.
    required_adapter_capabilities: set[AdapterCapability] = Field(default_factory=set)

    # Forward-compat: lets us evolve this schema while hidden / external
    # task manifests continue to validate.
    schema_version: str = "1"

    @model_validator(mode="after")
    def _defaults(self) -> CanonicalTask:
        if not self.variant_group:
            self.variant_group = self.id
        if not self.prompt_variants:
            self.prompt_variants = [PromptVariant.CLEAR]
        else:
            deduped: list[PromptVariant] = []
            for variant in self.prompt_variants:
                if variant not in deduped:
                    deduped.append(variant)
            self.prompt_variants = deduped
        return self
