"""Agent adapter ABC and associated data shapes.

An `AgentAdapter` is the execution counterpart to a `CanonicalTask`. It
is the only place where framework-specific details (OpenClaw gateway
RPCs, Hermes `MiniSWERunner`, Claude Code SDK, etc.) live. Everything
downstream of the adapter — trajectory analysis, scorer, judge, stats —
consumes a canonical `Transcript` and `TaskRunResult` produced by the
adapter, so those modules stay unchanged across adapters.

Lifecycle per task run:

1. Harness instantiates `adapter = AdapterClass(config)`.
2. `async with adapter as adapter:` — starts subprocesses / websockets
   / whatever this adapter needs to hold open across a run.
3. `await adapter.setup(ctx)` — realizes seed state, workspace files,
   background services, pre-run state queries.
4. For each `CanonicalPhase`: `await adapter.run_phase(phase, ctx)` —
   drives the simulated user against the agent, returns a
   `PhaseResult` with the transcript increment.
5. For each `StateQuery` in `task.verifier.state_queries`:
   `await adapter.verify_state_query(query, ctx)` — returns whether
   the assertion held, or that the adapter lacks the capability.
6. `await adapter.teardown(ctx)` — cleans up agent-side state (the
   workspace itself is harness-owned).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from clawbench.canonical import (
    AdapterCapability,
    CanonicalPhase,
    CanonicalTask,
    StateQuery,
)
from clawbench.schemas import Transcript, TranscriptMessage


@dataclass
class AdapterConfig:
    """Base config every adapter accepts.

    Adapters subclass this to add their own fields. The harness builds
    a config instance from CLI flags / env vars and passes it to the
    adapter constructor.
    """

    #: Primary model identifier. Semantics are adapter-specific (an
    #: OpenClaw model id, a Hermes `--model` string, etc.).
    model: str = ""


@dataclass
class AdapterContext:
    """Per-run context handed to every adapter method.

    `transcript` is mutated in place across phases: each
    `run_phase` call appends the messages it observed, so the scorer
    sees one consolidated `Transcript` at the end.
    """

    task: CanonicalTask
    workspace: Path
    runtime_values: dict[str, Any]
    run_index: int
    model: str
    transcript: Transcript
    #: Free-form adapter-owned scratch state (e.g. the OpenClaw
    #: `session_key` and `agent_id`; the Hermes `MiniSWERunner`
    #: instance). The harness never reads these — the adapter is free
    #: to use the dict as its own in-context cache.
    adapter_state: dict[str, Any] = field(default_factory=dict)


@dataclass
class PhaseResult:
    """The transcript increment produced by a single phase."""

    messages: list[TranscriptMessage] = field(default_factory=list)
    #: Adapter-specific metadata for this phase (token counts returned
    #: by the adapter, session identifiers, etc.). Merged into
    #: `TaskRunResult` under the `efficiency_result` / adapter metadata
    #: fields where applicable.
    adapter_metadata: dict[str, Any] = field(default_factory=dict)
    #: True if the adapter detected that the agent completed normally
    #: (e.g. Hermes's `completed=True`). Not a pass/fail signal — just
    #: whether the trajectory ran out of work vs was cut short. The
    #: scorer uses this in `delivery_outcome` classification.
    completed_normally: bool = True
    #: If the phase aborted due to the adapter itself (not the agent),
    #: populated with an error message the harness surfaces.
    error: str | None = None


@dataclass
class StateQueryResult:
    """Result of resolving a `StateQuery` against the adapter's state.

    `capability_missing=True` means "this adapter cannot evaluate this
    kind of query". The scorer treats that as neutral (neither pass nor
    fail) and records a skip note in the `CompletionResult`; under
    `--strict-compat` the harness will have filtered the task out before
    the adapter ever saw it.
    """

    ok: bool
    detail: str = ""
    capability_missing: bool = False


class AgentAdapter(ABC):
    """Abstract base class for agent adapters.

    Subclasses MUST:
    - Set a unique `name: ClassVar[str]`.
    - Set a `capabilities: ClassVar[set[AdapterCapability]]` declaring
      which state-query kinds the adapter can resolve.
    - Implement `setup`, `run_phase`, `verify_state_query`, `teardown`.
    - Optionally implement `__aenter__` / `__aexit__` for long-lived
      resource setup (a persistent websocket, a subprocess pool).
    """

    name: ClassVar[str] = ""
    capabilities: ClassVar[set[AdapterCapability]] = set()

    def __init__(self, config: AdapterConfig | None = None) -> None:
        self.config: AdapterConfig = config or AdapterConfig()

    # ------------------------------------------------------------------
    # Optional long-lived resource management.
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AgentAdapter":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    # ------------------------------------------------------------------
    # Required per-run lifecycle.
    # ------------------------------------------------------------------

    @abstractmethod
    async def setup(self, ctx: AdapterContext) -> None:
        """Realise the workspace, seed state, and any pre-run state.

        The harness has already created the workspace dir and expanded
        `CanonicalAssets.workspace_files` into it. The adapter is
        responsible for:

        - Applying `seed_state` entries via an adapter-appropriate
          mechanism (OpenClaw → memory RPCs; Hermes → file writes).
        - Starting the agent's process/session so `run_phase` can send
          turns immediately.
        """

    @abstractmethod
    async def run_phase(
        self,
        phase: CanonicalPhase,
        ctx: AdapterContext,
    ) -> PhaseResult:
        """Drive one `CanonicalPhase` to completion.

        The simulated user in `phase.user` dictates what to send and
        when. The adapter's job is to deliver those turns, observe the
        agent's responses, and append canonical `TranscriptMessage`
        entries to `ctx.transcript`.
        """

    @abstractmethod
    async def verify_state_query(
        self,
        query: StateQuery,
        ctx: AdapterContext,
    ) -> StateQueryResult:
        """Resolve one `StateQuery` against the agent's post-run state.

        Adapters whose `capabilities` don't cover `query.required_capability`
        should return `StateQueryResult(ok=False, capability_missing=True)`.
        """

    @abstractmethod
    async def teardown(self, ctx: AdapterContext) -> None:
        """Release any agent-side state created during `setup`/`run_phase`.

        The harness owns the workspace lifecycle; the adapter owns
        sessions, subprocesses, and any in-memory caches it held open.
        """

    # ------------------------------------------------------------------
    # Convenience helpers available to every adapter.
    # ------------------------------------------------------------------

    @classmethod
    def supported_capabilities(
        cls,
        config: AdapterConfig | None = None,
    ) -> set[AdapterCapability]:
        """Return capabilities available for a concrete adapter config.

        Most adapters have a fixed surface and can use the class-level
        `capabilities`. Adapters with multiple driver modes, such as Hermes
        MiniSWE vs full AIAgent, override this to keep task gating honest.
        """

        return set(cls.capabilities)

    @classmethod
    def missing_capabilities_for(
        cls,
        task: CanonicalTask,
        config: AdapterConfig | None = None,
    ) -> set[AdapterCapability]:
        """Return the subset of `task.required_adapter_capabilities` this
        adapter cannot cover. Empty set means the task is fully runnable
        under this adapter.
        """

        return set(task.required_adapter_capabilities) - cls.supported_capabilities(config)

    @classmethod
    def supports(
        cls,
        task: CanonicalTask,
        config: AdapterConfig | None = None,
    ) -> bool:
        """True iff this adapter can cover every capability the task needs."""

        return not cls.missing_capabilities_for(task, config)
