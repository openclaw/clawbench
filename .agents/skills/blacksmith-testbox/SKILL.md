---
name: blacksmith-testbox
description: Run Blacksmith Testbox for ClawBench CI parity, live credentials, Docker builds, and benchmark sweeps.
---

# Blacksmith Testbox

Use Testbox when ClawBench work needs CI parity, org-level secrets, hydrated
agent dotfiles, Docker, or a benchmark run that is too heavy for the local
machine. Keep normal unit-test iteration local unless the user asks for
Testbox proof.

## Warmup

Run from the repository root:

```bash
blacksmith testbox warmup ci-check-testbox.yml --ref main --idle-timeout 90
```

Save the returned `tbx_...` ID and reuse it for every command in the same
task. Stop boxes you create when done:

```bash
blacksmith testbox stop --id <ID>
```

## Commands

Always invoke `blacksmith testbox` from the repo root. The CLI syncs the
current git working tree to the remote box; running from a subdirectory can
delete the rest of the remote checkout.

```bash
blacksmith testbox run --id <ID> "python -m pytest -q"
blacksmith testbox run --id <ID> "python -m pip wheel --no-deps . -w /tmp/clawbench-wheel"
blacksmith testbox run --id <ID> "docker build -t clawbench ."
```

If a command needs HF/provider credentials or agent dotfiles, wrap it with the
hydrated helper installed by the workflow:

```bash
blacksmith testbox run --id <ID> "clawbench-testbox-env python -m pytest -q"
blacksmith testbox run --id <ID> "clawbench-testbox-env clawbench run --model anthropic/claude-sonnet-4-6 --adapter simulated"
```

## Sync Model

The testbox starts from a clean checkout and installed Python environment.
Tracked and untracked non-ignored files are synced before each `run`.
Ignored files such as `.venv/`, `data/`, `.pytest_cache/`, and `dist/` are
not synced. If `pyproject.toml` changes, rerun install remotely:

```bash
blacksmith testbox run --id <ID> "python -m pip install -e . && python -m pytest -q"
```

## Hydrated Secrets And Dotfiles

The workflow writes non-empty provider and HF secrets to
`~/.clawbench-testbox-live.profile`, and installs `~/.local/bin/clawbench-testbox-env`
to source that profile. It also restores optional agent dotfiles from either
ClawBench-specific secrets or the existing OpenClaw org-level secret names:

- `~/.codex/auth.json`
- `~/.codex/config.toml`
- `~/.claude.json`
- `~/.claude/.credentials.json`
- `~/.claude/settings.json`
- `~/.claude/settings.local.json`
- `~/.gemini/settings.json`

Prefer org-level secrets where possible; Blacksmith runner access is org-level,
not repo-specific.
