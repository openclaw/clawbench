---
title: ClawBench
emoji: 🦞
colorFrom: red
colorTo: yellow
sdk: docker
app_port: 7860
pinned: true
license: mit
---

<div align="center">

# ClawBench

**Trace-scored agent evaluation for OpenClaw.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB.svg?style=flat-square)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg?style=flat-square)](LICENSE)
[![Core v1: 19 tasks](https://img.shields.io/badge/Core%20v1-19%20tasks-blue.svg?style=flat-square)](tasks-public/)
[![HF Dataset](https://img.shields.io/badge/HF-dataset-yellow.svg?style=flat-square)](https://huggingface.co/datasets/openclaw/clawbench-results)

</div>

---

## What This Repo Contains

ClawBench evaluates AI agents by running real local tasks, capturing the
execution trace, and scoring both the final state and the process used to get
there.

The public repository contains:

- `tasks-public/`: Core v1, a 19-task public reproducibility suite.
- `clawbench/`: the benchmark harness, adapters, canonical task conversion,
  scoring, statistics, and diagnostics.
- `profiles/`: example model/profile definitions.
- `scripts/`: reusable analysis and container runner utilities.
- `tests/`: unit and integration coverage for the public harness.

The private holdout is intentionally not included:

- private task YAML files,
- private task assets and verifier scripts,
- private expected outputs,
- private run traces, logs, and per-task reports.

Internal hidden-suite runs can restore a private `tasks/` directory locally.
The public code is designed to run without that directory by falling back to
`tasks-public/`.

## Core v1

Core v1 is a signal-curated 19-task public release selected from the internal
development pool. It preserves tier and family coverage while avoiding tasks
whose public release would leak holdout material or add mostly run-to-run
noise.

| Dimension | Breakdown |
|---|---|
| Tasks | 19 |
| Runs per official comparison | 3 per task |
| Total runs per model | 57 |
| Tiers | T1=2, T2=6, T3=5, T4=5, T5=1 |
| Families | tools=8, coding=2, repo=3, browser=2, multi_tool=3, adversarial=1 |

The manifest is the source of truth:

```bash
python3 - <<'PY'
import yaml
manifest = yaml.safe_load(open("tasks-public/MANIFEST.yaml"))
for task in manifest["tasks"]:
    print(task["id"])
PY
```

## Scoring

Each run is scored from four signals:

| Axis | Weight | What it measures |
|---|---:|---|
| Completion | 40% | Deterministic task checks such as tests, exact outputs, DOM assertions, and file verification |
| Trajectory | 30% | Tool-use quality such as read-before-write, self-verification, recovery, and tool-family fit |
| Behavior | 20% | Planning, progress updates, blocker handling, and destructive-command avoidance |
| Judge | Up to 10% | Optional semantic quality, gated so it cannot rescue failed deterministic checks |

Reliability is first-class. Official comparisons run each task three times and
report per-task variance, pass rate, pass^k, confidence intervals, and
worst-of-n style robustness signals.

## Quick Start

Install locally:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

List public tasks:

```bash
clawbench list-tasks --tasks-dir tasks-public
```

Run a small public smoke:

```bash
export OPENCLAW_GATEWAY_TOKEN=<your-token>

clawbench run \
  --model anthropic/claude-opus-4-6 \
  --runs 1 \
  --task t1-bugfix-discount \
  --task t1-fs-quick-note \
  --output results/public_smoke.json
```

Run the full Core v1 task list:

```bash
TASK_ARGS=$(python3 - <<'PY'
import yaml
manifest = yaml.safe_load(open("tasks-public/MANIFEST.yaml"))
print(" ".join(f"--task {task['id']}" for task in manifest["tasks"]))
PY
)

clawbench run \
  --model anthropic/claude-opus-4-6 \
  --runs 3 \
  --concurrency 4 \
  $TASK_ARGS \
  --output results/core_v1_opus46.json
```

Build the public Space image:

```bash
docker build -t clawbench .
docker run --rm --entrypoint openclaw clawbench --version
```

## Hidden-Suite Reproduction

The hidden full-suite runner is public, but the task content is not. To rerun
an internal hidden-suite comparison, restore the private task archive into
`./tasks/` before building the hidden eval image. Do not commit that directory,
its logs, or generated per-task traces.

```bash
docker build -f Dockerfile.openclaw-426-agent-hotfix \
  -t openclaw-426-agent-hotfix:latest .

docker build -f Dockerfile.clawbench-426-agent-hotfix \
  -t clawbench-openclaw-426-agent-hotfix:latest .
```

The public repo intentionally does not include exact private task IDs, prompts,
assets, expected artifacts, or trace-derived private reports.

## Analysis Tools

Reusable scripts that operate on public or private result archives:

- `scripts/container_lane_eval.sh`: isolated OpenClaw lane runner.
- `scripts/container_adapter_eval.sh`: adapter/model runner for fair adapter comparisons.
- `scripts/run_posterior_dynamics_pipeline.py`: one-shot offline dynamics analysis.
- `scripts/compute_constraint_index.py`: task-level constraint index.
- `scripts/variance_decomp.py`: seed-noise vs capability-signal decomposition.
- `scripts/survival_analysis.py`: per-turn failure survival curves.
- `scripts/snr_weighted_ranking.py`: SNR-weighted ranking.

Generated data, traces, and reports are local artifacts and are ignored by Git.

## Repository Layout

```text
clawbench/
├── clawbench/                  # Harness, adapters, scoring, diagnostics
├── tasks-public/               # Core v1 public task suite
├── tasks-domain/               # Domain expansion scaffold
├── profiles/                   # Model/profile definitions
├── scripts/                    # Reusable runners and offline analysis
├── tests/                      # Public test suite
├── Dockerfile                  # Public HF Space image
├── Dockerfile.main             # Main-variant public image
├── Dockerfile.openclaw-426-agent-hotfix
├── Dockerfile.clawbench-426-agent-hotfix
├── CLAWBENCH_V0_4_SPEC.md
└── PARTNER_TRACE_SPEC.md
```

## Testing

```bash
python -m pytest -q
```

The test suite includes public-surface checks to keep the README and Space
description aligned with `tasks-public/MANIFEST.yaml`.

## License

MIT. See `LICENSE`.

## Citation

```bibtex
@software{clawbench,
  title  = {ClawBench: Trace-Scored Agent Benchmark with Dynamical-Systems Diagnostics},
  author = {ScoootScooob},
  year   = {2026},
  url    = {https://github.com/openclaw/clawbench}
}
```
