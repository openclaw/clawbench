# Release 2026.2 Rollout Plan

## Feature flags to configure

The following three facts need to land in `flags.py`:

- **Beta rollout regions**: `us`, `eu`
- **Retry budget**: `3`
- **APAC gating**: APAC remains gated until release `2026.3` (store this as `APAC_GATED_UNTIL`).

## Operational notes (informational only — NOT flags)

- Nightly build cadence: every 4 hours.
- Release captain on-call: alice@example.com.
- Feature freeze: 2026-04-30.

The operational notes above do NOT need to be codified into `flags.py` —
they are team-facing context only. Only the three flags in the first
section belong in code.
