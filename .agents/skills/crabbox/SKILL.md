---
name: crabbox
description: Use Crabbox for ClawBench remote Linux validation. Default to Blacksmith Testbox; includes direct Blacksmith and owned AWS fallback notes when Crabbox fails.
---

# Crabbox

Use Crabbox when ClawBench needs remote Linux proof for CI-parity checks, broad
Python suites, package/build lanes, provider/HF secrets, hydrated agent
dotfiles, benchmark sweeps, warmed reusable boxes, sync timing, logs/results,
cache inspection, or lease cleanup.

Default backend: `blacksmith-testbox`. The separate `blacksmith-testbox` skill
has been removed; this skill owns both the normal Crabbox path and the direct
Blacksmith fallback playbook.

## First Checks

- Run from the repo root. Crabbox sync mirrors the current checkout.
- Check the wrapper and provider support before remote work:

```sh
command -v crabbox
../crabbox/bin/crabbox --version
crabbox run --provider blacksmith-testbox --help | sed -n '1,140p'
```

- Prefer the sibling `openclaw/crabbox` checkout at `../crabbox/bin/crabbox`
  when present. The user PATH shim can be stale.
- Check `.crabbox.yaml` for repo-owned AWS defaults, but override the provider
  explicitly for normal maintainer validation. Even if config still says AWS,
  broad ClawBench proof should normally pass `--provider blacksmith-testbox`.
- Prefer local targeted tests for tight edit loops. Broad suites, package
  checks, Docker/container sweeps, live/provider lanes, and benchmark sweeps
  belong remote.
- Do not move broad gates back onto the laptop just because Testbox is queued or
  capacity is constrained. Keep targeted local edit-loop checks narrow, leave
  the remote lane queued, or report the capacity blocker.
- For live/provider bugs, check keys on the local Mac before downgrading to
  mocks. Source local `~/.profile` and test only presence/length. If Crabbox
  does not already have the key, copy only the exact needed key into the remote
  process environment for that one command. Do not print it, sync it as a repo
  file, or leave it in remote shell history or logs.

## Default Blacksmith Backend

Use this for `python -m pytest -q`, full Ruff lint, Docker/container sweeps,
package/build checks, provider/HF lanes, or anything likely to fan out beyond a
small targeted test.

Full test suite:

```sh
crabbox run --provider blacksmith-testbox \
  --blacksmith-org openclaw \
  --blacksmith-workflow .github/workflows/ci-check-testbox.yml \
  --blacksmith-job check \
  --blacksmith-ref main \
  --idle-timeout 90m \
  --ttl 240m \
  --timing-json \
  --shell -- \
  "python -m pytest -q"
```

Lint plus tests:

```sh
crabbox run --provider blacksmith-testbox \
  --blacksmith-org openclaw \
  --blacksmith-workflow .github/workflows/ci-check-testbox.yml \
  --blacksmith-job check \
  --blacksmith-ref main \
  --idle-timeout 90m \
  --ttl 240m \
  --timing-json \
  --shell -- \
  "python -m ruff check clawbench app.py scripts tests && python -m pytest -q"
```

Focused rerun:

```sh
crabbox run --provider blacksmith-testbox \
  --blacksmith-org openclaw \
  --blacksmith-workflow .github/workflows/ci-check-testbox.yml \
  --blacksmith-job check \
  --blacksmith-ref main \
  --idle-timeout 90m \
  --ttl 240m \
  --timing-json \
  --shell -- \
  "python -m pytest -q tests/test_blacksmith_setup.py"
```

Commands that need hydrated HF/provider credentials or restored agent dotfiles
should use the helper installed by the workflow:

```sh
crabbox run --provider blacksmith-testbox \
  --blacksmith-org openclaw \
  --blacksmith-workflow .github/workflows/ci-check-testbox.yml \
  --blacksmith-job check \
  --blacksmith-ref main \
  --idle-timeout 90m \
  --ttl 240m \
  --timing-json \
  --shell -- \
  "clawbench-testbox-env clawbench run --model anthropic/claude-sonnet-4-6 --adapter simulated"
```

Read the JSON summary. Useful fields:

- `provider`: should be `blacksmith-testbox`
- `leaseId`: should usually be a `tbx_...` id
- `syncDelegated`: should be `true`
- `commandMs` / `totalMs`
- `exitCode`

Crabbox should stop one-shot Blacksmith Testboxes automatically after the run.
Verify cleanup when a run fails, is interrupted, or output is unclear:

```sh
blacksmith testbox list
```

## Efficient Verification

Use the smallest remote lane that proves the reported user path, not just the
touched code.

- Python/runtime bug: run the focused test locally during the edit loop, then
  prove the suite or affected file remotely through Crabbox.
- Package/build bug: build the wheel or image remotely from the synced checkout.
- Provider/HF bug: prefer true live proof through `clawbench-testbox-env`. If a
  dummy key is used, label the result narrowly as install/UI behavior only.
- Benchmark/sweep bug: use the existing ClawBench command surface and record
  model/profile, adapter, task subset, and output path.
- Pure docs or metadata: `git diff --check` is usually enough unless the docs
  change executable commands, workflows, package metadata, or user-visible
  validation instructions.

Efficient flow:

1. Reproduce or characterize the pre-fix symptom when feasible.
2. Patch locally and run narrow local tests for edit speed.
3. Run one Crabbox command that starts from the relevant user-facing entrypoint:
   tests, lint, package build, container script, or `clawbench run`.
4. Record proof as: Testbox id, command, environment shape, redacted secret
   source, and copied success/failure output.
5. If the issue says "cannot reproduce", ask for the missing config/log fields
   that distinguish the tested path from the reporter's path.

## Reuse And Keepalive

For most Blacksmith-backed Crabbox calls, one-shot is enough. Use reuse only
when several commands must share the same hydrated box, installed packages,
cached images, or benchmark state.

If Crabbox returns a reusable id or you intentionally keep a lease:

```sh
crabbox run --provider blacksmith-testbox --id <tbx_id> --no-sync --timing-json --shell -- "python -m pytest -q tests/test_blacksmith_setup.py"
```

Stop boxes you created before handoff:

```sh
crabbox stop <id-or-slug>
blacksmith testbox stop --id <tbx_id>
```

## If Crabbox Fails

Keep the fallback narrow. First decide whether the failure is Crabbox itself,
Blacksmith/Testbox, repo hydration, sync, or the test command.

Fast checks:

```sh
command -v crabbox
../crabbox/bin/crabbox --version
crabbox run --provider blacksmith-testbox --help | sed -n '1,140p'
command -v blacksmith
blacksmith --version
blacksmith testbox list
```

Common Crabbox-only failures:

- Provider missing or old CLI: use `../crabbox/bin/crabbox` from the sibling
  repo, or update/install Crabbox before retrying.
- Bad local config: pass `--provider blacksmith-testbox` plus explicit
  `--blacksmith-*` flags instead of relying on `.crabbox.yaml`.
- Slug/claim confusion: use the raw `tbx_...` id, or run one-shot without
  `--id`.
- Sync/timing bug: add `--debug --timing-json`; capture the final JSON and the
  printed Actions URL.
- Cleanup uncertainty: run `blacksmith testbox list` and stop only boxes you
  created.
- Testbox queued/capacity pressure: keep the broad proof remote; run only a
  focused local check or report the blocker.

If Crabbox cannot dispatch, sync, attach, or stop but Blacksmith itself works,
use direct Blacksmith from the repo root:

```sh
blacksmith testbox warmup ci-check-testbox.yml --ref main --idle-timeout 90
blacksmith testbox run --id <tbx_id> "python -m pytest -q"
blacksmith testbox stop --id <tbx_id>
```

Direct lint plus tests:

```sh
blacksmith testbox run --id <tbx_id> "python -m ruff check clawbench app.py scripts tests && python -m pytest -q"
```

If `pyproject.toml` or dependency setup changes, rerun install remotely:

```sh
blacksmith testbox run --id <tbx_id> "python -m pip install -e .[dev] && python -m pytest -q"
```

Auth fallback, only when `blacksmith` says auth is missing:

```sh
blacksmith auth login --non-interactive --organization openclaw
```

Direct Blacksmith footguns:

- Run from repo root. The CLI syncs the current directory.
- Save the returned `tbx_...` id in the session.
- Reuse that id for focused reruns; stop it before handoff.
- Raw commit SHAs are not reliable `warmup --ref` refs; use a branch or tag.
- Treat `blacksmith testbox list` as cleanup diagnostics, not a shared reusable
  queue.

## Owned AWS Fallback

Use AWS only when Blacksmith is down, quota-limited, missing the needed
environment, or owned capacity is explicitly the goal. The repo `.crabbox.yaml`
keeps these owned-capacity defaults on purpose.

AWS/owned-capacity flow:

```sh
crabbox warmup --provider aws --class standard --idle-timeout 90m
crabbox actions hydrate --id <cbx_id-or-slug>
crabbox run --id <cbx_id-or-slug> --timing-json --shell -- "python -m pytest -q"
crabbox stop <cbx_id-or-slug>
```

For commands that need hydrated HF/provider credentials or agent dotfiles:

```sh
crabbox run --id <cbx_id-or-slug> --timing-json --shell -- "clawbench-testbox-env python -m pytest -q"
crabbox run --id <cbx_id-or-slug> --timing-json --shell -- "clawbench-testbox-env clawbench run --model anthropic/claude-sonnet-4-6 --adapter simulated"
```

When AWS capacity is under pressure, do not start with `class=beast`.
`beast` begins at 48xlarge instances and can burn 192 vCPU quota per request.
ClawBench's owned-cloud default is `standard`; escalate to `fast`, then
`large`, and only use `beast` when the work is explicitly CPU-bound and the
smaller class already failed the goal.

Install/auth for owned Crabbox if needed:

```sh
brew install openclaw/tap/crabbox
crabbox login --url https://crabbox.openclaw.ai --provider aws
crabbox doctor
crabbox whoami
```

macOS config lives at:

```text
~/Library/Application Support/crabbox/config.yaml
```

It should include `broker.url`, `broker.token`, and usually `provider: aws`
for owned-cloud lanes. Do not let that config override the ClawBench default
when Blacksmith proof is requested; pass `--provider blacksmith-testbox`.

## Diagnostics

```sh
crabbox status --id <id-or-slug> --wait
crabbox inspect --id <id-or-slug> --json
crabbox sync-plan
crabbox history --lease <id-or-slug>
crabbox logs <run_id>
crabbox results <run_id>
crabbox cache stats --id <id-or-slug>
crabbox ssh --id <id-or-slug>
blacksmith testbox list
```

Use `--debug` on `run` when measuring sync timing.
Use `--timing-json` on broad or flaky runs when command duration or sync
behavior matters.
Use `--market spot|on-demand` only on AWS warmup or one-shot AWS runs.

## Hydration Boundary

The Blacksmith workflow `.github/workflows/ci-check-testbox.yml` and the AWS
hydration workflow `.github/workflows/crabbox-hydrate.yml` are repo-specific on
purpose. They own ClawBench checkout, setup-python, pip install,
provider/HF env hydration, agent-dotfile restoration, ready marker, and
keepalive. Crabbox owns dispatch, sync, SSH command execution, logs/results,
timing, and cleanup.

Do not add ClawBench-specific setup to Crabbox itself. Put repo setup in the
hydration workflows and keep Crabbox generic around lease, sync, command
execution, logs/results, timing, and cleanup.
