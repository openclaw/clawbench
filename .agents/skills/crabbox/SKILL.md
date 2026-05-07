---
name: crabbox
description: Use Crabbox for ClawBench remote Linux validation, warmed reusable boxes, GitHub Actions hydration, sync timing, logs, results, caches, and lease cleanup.
---

# Crabbox

Use Crabbox when ClawBench needs remote Linux proof on owned capacity, a large
runner class, reusable warm state, or a Blacksmith alternative.

## Before Running

- Run from the repo root. Crabbox sync mirrors the current checkout.
- Prefer local targeted tests for tight edit loops.
- Prefer Blacksmith Testbox when the task explicitly asks for Blacksmith or a
  Blacksmith-specific CI comparison.
- Use Crabbox for broad ClawBench gates when owned AWS capacity is the right
  remote lane.
- Check `.crabbox.yaml` for repo defaults before adding flags.
- Sanity-check the selected binary before remote work. Prefer the local
  `openclaw/crabbox` checkout when present because the user PATH shim can be
  stale: `command -v crabbox; ../crabbox/bin/crabbox --version`.
- Install with `brew install openclaw/tap/crabbox`; auth is required before use:
  `crabbox login --url https://crabbox.openclaw.ai --provider aws`.
- On macOS the user config is `~/Library/Application Support/crabbox/config.yaml`;
  it must include `broker.url`, `broker.token`, and usually `provider: aws`.

## ClawBench Flow

AWS/owned-capacity flow for Python tests:

```sh
crabbox warmup --class standard --idle-timeout 90m
crabbox actions hydrate --id <cbx_id-or-slug>
crabbox run --id <cbx_id-or-slug> --timing-json --shell -- "python -m pytest -q"
```

For commands that need hydrated HF/provider credentials or agent dotfiles, use
the helper installed by the hydration workflow:

```sh
crabbox run --id <cbx_id-or-slug> --timing-json --shell -- "clawbench-testbox-env python -m pytest -q"
crabbox run --id <cbx_id-or-slug> --timing-json --shell -- "clawbench-testbox-env clawbench run --model anthropic/claude-sonnet-4-6 --adapter simulated"
```

Blacksmith-backed Crabbox flow can delegate setup to the existing Testbox
workflow:

```sh
crabbox run --provider blacksmith-testbox --blacksmith-org openclaw --blacksmith-workflow .github/workflows/ci-check-testbox.yml --blacksmith-job check --blacksmith-ref main --idle-timeout 90m --timing-json --shell -- "python -m pytest -q"
```

Stop boxes you created before handoff:

```sh
crabbox stop <cbx_id-or-slug>
```

## Owned AWS Capacity

When AWS capacity is under pressure, do not start with `class=beast`.
`beast` begins at 48xlarge instances and can burn 192 vCPU quota per request.
ClawBench's owned-cloud default is `standard`; escalate to `fast`, then
`large`, and only use `beast` when the work is explicitly CPU-bound and the
smaller class already failed the goal.

Keep capacity hints enabled so brokered AWS leases print selected
region/market, quota pressure, Spot fallback, and high-pressure class warnings.
The ClawBench repo config sets `capacity.hints: true`; use
`CRABBOX_CAPACITY_HINTS=0` only when debugging hint rendering itself.

Use `beast` only for exceptional lanes:

- full benchmark sweeps where wall time is dominated by CPU, not dependency
  install or network;
- release/blocker validation where a maintainer explicitly asks for the largest
  owned AWS class;
- performance profiling where the point is to compare high-core behavior.

Do not use `beast` for ordinary `python -m pytest -q`, docs-only work, small
task repros, Blacksmith outage triage, or focused lint/type/test checks. Those
should use `standard` first and `fast` only when the extra cores materially
help.

## Useful Commands

```sh
crabbox status --id <id-or-slug> --wait
crabbox inspect --id <id-or-slug> --json
crabbox sync-plan
crabbox history --lease <id-or-slug>
crabbox logs <run_id>
crabbox results <run_id>
crabbox cache stats --id <id-or-slug>
crabbox ssh --id <id-or-slug>
```

Use `--debug` on `run` when measuring sync timing.
Use `--timing-json` on warmup, hydrate, and run when comparing AWS and
blacksmith-testbox timings.
Use `--market spot|on-demand` on AWS warmup or one-shot run when testing quota
or capacity behavior without changing `.crabbox.yaml`.

## Hydration Boundary

`.github/workflows/crabbox-hydrate.yml` is repo-specific on purpose. It owns
ClawBench checkout, setup-python, pip install, provider/HF env hydration,
agent-dotfile restoration, ready marker, and keepalive. Crabbox owns runner
registration, workflow dispatch, SSH sync, command execution, logs/results,
local lease claims, and idle cleanup.

Do not add ClawBench-specific setup to Crabbox. Put repo setup in the hydration
workflow and generic lease/sync behavior in Crabbox.

## Cleanup

Crabbox has coordinator-owned idle expiry and local lease claims, so ClawBench
does not need a custom ledger. Default idle timeout is 30 minutes unless config
or flags set a different value. Still stop boxes you created when done.
If `crabbox list` prints `orphan=no-active-lease`, treat it as an operator
review hint; do not delete `keep=true` machines without checking provider and
coordinator state.
