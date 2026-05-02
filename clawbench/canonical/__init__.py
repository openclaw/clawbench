"""Canonical task schema — agent-agnostic intent layer.

Part of ClawBench Phase-4 per CLAWBENCH_V0_4_SPEC.md §"Canonical Task Schema".
Splits canonical task intent (what to set up, prompt with, and verify) from
OpenClaw-specific execution details (which become adapter responsibilities).

The existing `TaskDefinition` in `clawbench/schemas.py` stays as-is for
back-compat; this package adds a canonical view produced by
`convert.from_task_definition`, which is the single bridge between the two
shapes. Everything downstream of the harness (scorer, trajectory, judge,
stats) is already agent-agnostic — those modules consume the transcript +
TaskRunResult and do not need changes.
"""

from clawbench.canonical.schema import (
    AdapterCapability,
    BudgetSpec,
    CanonicalAssets,
    CanonicalPhase,
    CanonicalTask,
    Deliverable,
    InteractionPolicy,
    SeedEntry,
    StateQuery,
    StateQueryKind,
    StateQueryPredicate,
    VerifierContract,
)
from clawbench.canonical.convert import from_task_definition

__all__ = [
    "AdapterCapability",
    "BudgetSpec",
    "CanonicalAssets",
    "CanonicalPhase",
    "CanonicalTask",
    "Deliverable",
    "InteractionPolicy",
    "SeedEntry",
    "StateQuery",
    "StateQueryKind",
    "StateQueryPredicate",
    "VerifierContract",
    "from_task_definition",
]
