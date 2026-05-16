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

# ClawBench

Execution-first benchmark for AI models acting as OpenClaw agents.

## Benchmark Shape

```text
public suite   : Core v1
tasks          : 19
runs/model     : 57 for official Core v1 comparisons
tiers          : 5
browser tasks  : 2
primary metric : trace-scored task score plus reliability
```

## What Gets Scored

| Layer | Verification style |
|---|---|
| Completion | `pytest`, exact output checks, browser flow checks, file checks, and verifier scripts |
| Trajectory | read-before-write, self-verification, recovery quality, tool-family fit, and safety rules |
| Behavior | deterministic transcript checks for planning, progress, blockers, and safe handling |
| Reliability | repeated runs with pass^k, pass rate, and score variance |

The advisory judge is optional and cannot replace deterministic verification.

## Runtime Flow

```text
task yaml + assets
  -> isolated workspace
  -> evaluator-owned verifier snapshot
  -> optional local background services
  -> OpenClaw agent session
  -> transcript + tool-result capture
  -> completion / trajectory / behavior scoring
  -> reliability aggregation
```

Completion verifiers, expected outputs, and hidden answer metadata are staged
outside the agent workspace during official harness runs. Public tests can stay
visible for self-verification, but the harness hash-checks protected files and
scores against the evaluator copy.

## Public Task Inventory

The Space uses `tasks-public/MANIFEST.yaml` as the source of truth. Current
Core v1 tasks are:

| Task | Tier | Family |
|---|---|---|
| `t1-bugfix-discount` | tier1 | coding |
| `t1-fs-quick-note` | tier1 | tools |
| `t2-add-tests-normalizer` | tier2 | coding |
| `t2-browser-form-fix` | tier2 | browser |
| `t2-config-loader` | tier2 | repo |
| `t2-fs-find-that-thing` | tier2 | tools |
| `t2-msg-summarize-thread` | tier2 | tools |
| `t2-priv-redact-doc` | tier2 | tools |
| `t3-data-pipeline-report` | tier3 | multi_tool |
| `t3-data-sql-query` | tier3 | tools |
| `t3-feature-export` | tier3 | repo |
| `t3-msg-inbox-triage` | tier3 | tools |
| `t3-web-research-and-cite` | tier3 | tools |
| `t4-browser-research-and-code` | tier4 | browser |
| `t4-cross-repo-migration` | tier4 | repo |
| `t4-delegation-repair` | tier4 | multi_tool |
| `t4-life-trip-plan` | tier4 | tools |
| `t4-memory-recall-continuation` | tier4 | multi_tool |
| `t5-hallucination-resistant-evidence` | tier5 | adversarial |

## Holdout Policy

Private task bodies, assets, expected outputs, verifier details, run traces,
logs, and per-task private reports are not part of the public Space. Public
Core v1 is intended for reproducibility and development; hidden-suite runs use
the same harness with a private task directory restored locally.
