"""Agent adapter layer — Phase-4 of CLAWBENCH_V0_4_SPEC.md.

Adapters plug an agent framework (OpenClaw, Hermes, Codex, Claude Code,
Deerflow, …) into ClawBench's canonical task pipeline. Each adapter is
responsible for:

- Setting up the workspace + seed state from a `CanonicalTask`.
- Driving the agent through each `CanonicalPhase`'s simulated user.
- Returning a canonical `Transcript` so the scorer, trajectory analyser,
  and judge can score the run unchanged.
- Resolving `StateQuery` assertions that fall under its declared
  capabilities; returning `capability_missing=True` for queries that
  require a capability the adapter doesn't provide.

The `ADAPTERS` registry is populated by each adapter module at import
time. `get_adapter(name)` is the canonical lookup.
"""

from __future__ import annotations

from clawbench.adapters.base import (
    AdapterConfig,
    AdapterContext,
    AgentAdapter,
    PhaseResult,
    StateQueryResult,
)

#: Registry of adapter_name → adapter class. Populated by the adapter
#: modules at import time (e.g. `from clawbench.adapters.openclaw import *`
#: registers the OpenClaw adapter). Callers should use `get_adapter`
#: rather than reading this dict directly.
ADAPTERS: dict[str, type[AgentAdapter]] = {}


def register_adapter(cls: type[AgentAdapter]) -> type[AgentAdapter]:
    """Decorator / direct-call helper that registers an adapter class.

    Adapters declare themselves via:

    ```
    @register_adapter
    class HermesAdapter(AgentAdapter):
        name = "hermes"
        ...
    ```
    """

    name = getattr(cls, "name", "")
    if not name:
        raise ValueError(f"{cls.__name__} must set a non-empty `name` class attribute")
    existing = ADAPTERS.get(name)
    if existing is not None and existing is not cls:
        raise ValueError(
            f"Adapter name collision: '{name}' is already registered "
            f"to {existing.__qualname__}"
        )
    ADAPTERS[name] = cls
    return cls


def get_adapter(name: str) -> type[AgentAdapter]:
    """Look up an adapter class by its registered name.

    Import the adapter module before calling this so the registration
    has run. `clawbench.adapters.openclaw` always loads; optional
    adapters (hermes, codex) guard their imports and raise a clear
    error if their runtime dep isn't installed.
    """

    try:
        return ADAPTERS[name]
    except KeyError as exc:
        available = ", ".join(sorted(ADAPTERS)) or "(none)"
        raise KeyError(
            f"Unknown adapter '{name}'. Registered adapters: {available}"
        ) from exc


__all__ = [
    "ADAPTERS",
    "AdapterConfig",
    "AdapterContext",
    "AgentAdapter",
    "PhaseResult",
    "StateQueryResult",
    "get_adapter",
    "register_adapter",
]


# Register built-in adapters at import time. Each adapter module is
# expected to @register_adapter its class. OpenClaw is always
# available; optional adapters (hermes, codex) guard their imports and
# are registered only when their runtime dep is present.
from clawbench.adapters import openclaw as _openclaw  # noqa: E402,F401

try:
    from clawbench.adapters import hermes as _hermes  # noqa: E402,F401
except Exception:
    # hermes-agent is an optional extra; absence is fine.
    _hermes = None  # type: ignore[assignment]
