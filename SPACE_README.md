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

This Space evaluates models on realistic local agent tasks and scores them with a deterministic pipeline that emphasizes:

- **Completion**: did the work actually pass executable checks?
- **Trajectory**: did the agent explore, recover, and use tools well?
- **Behavior**: did the transcript show planning, progress updates, and safe handling?
- **Reliability**: was performance stable across repeated runs?

## Why this benchmark exists

ClawBench is built to avoid three common benchmark failures:

1. trusting what the agent said instead of running the work,
2. rewarding one reference trajectory instead of rewarding good agent properties,
3. hiding instability by reporting only one lucky run.

## Benchmark shape

```text
tasks          : 20
tiers          : 5
prompt modes   : clear + ambiguous on every task
browser tasks  : 2
multi-phase    : 1
judge-enabled  : 6 advisory tasks
primary metric : pass^k
```

### Tier mix

```text
tier1 | ###   3
tier2 | ##### 5
tier3 | ##### 5
tier4 | ####  4
tier5 | ###   3
```

### Family mix

```text
repo        | ###### 6
coding      | ####   4
multi_tool  | ###    3
adversarial | ###    3
browser     | ##     2
tools       | ##     2
```

## Official score stack

Per-run score:

```text
normalize(0.4 * completion + 0.3 * trajectory + 0.2 * behavior)
```

Per-task score after repeated runs:

```text
0.9 * mean_run_score + 0.1 * reliability_score
```

Reliability:

```text
0.5 * pass_hat_k + 0.3 * pass_rate + 0.2 * variance_score
```

## What gets verified

| Layer | Verification style |
| --- | --- |
| Completion | `pytest`, `node --test`, exact output checks, browser flow checks, cron checks, memory checks, gateway assertions |
| Trajectory | read-before-write, self-verification, recovery quality, tool-family fit, safety rules |
| Behavior | deterministic transcript rules for planning, progress, blocker handling, refusal quality, destructive-command avoidance |
| Reliability | repeated runs with pass^k, pass rate, and score variance |

The official score stays deterministic.

Optional advisory judge results are reported separately and never replace executable verification.

## Runtime flow

```text
task yaml + assets
  -> isolated workspace
  -> optional local background services
  -> OpenClaw agent session(s)
  -> transcript + tool-result capture
  -> completion / trajectory / behavior scoring
  -> repeated runs
  -> reliability aggregation
  -> leaderboard result
```

## Browser policy

Browser tasks in this Space are deterministic and local:

```text
task-owned local app or docs
  -> OpenClaw browser tool
  -> real browser interaction
  -> deterministic local verification
```

No public websites are used for official browser tasks.

## Parallel Space runtime

On upgraded CPU Spaces, the worker can use conservative parallel lanes:

```text
submission
  -> task partitioner
  -> lane 1 gateway + lane-local state
  -> lane 2 gateway + lane-local state
  -> browser lane gateway + lane-local state
  -> merged benchmark result
```

Important rule: browser tasks stay serialized on one dedicated lane to avoid Chromium and port-range collisions.

## Submission presets

The Submit tab now exposes two preset audiences so the Space can serve both general Claw users and lower-budget exploratory runs:

- `Claw Users` keeps the full preset catalog, including provider-backed frontier models.
- `Budget Researchers` narrows the list to local or lower-cost presets such as `ollama/gpt-oss:20b`, `ollama/qwen3.5:27b`, `huggingface/Qwen/Qwen3-32B`, and `huggingface/google/gemma-4-26B-A4B-it`.

You can still enter any custom model ID directly; the preset audience only filters the shortcut catalog and the bulk-submit action.

## Task inventory

| Task | Tier | Family | Main verification |
| --- | --- | --- | --- |
| `t1-architecture-brief` | tier1 | tools | fact verifier + smoke command |
| `t1-bugfix-discount` | tier1 | coding | `pytest` |
| `t1-refactor-csv-loader` | tier1 | coding | `pytest` + verification script |
| `t2-add-tests-normalizer` | tier2 | coding | `pytest` + added-test checks |
| `t2-browser-form-fix` | tier2 | browser | local browser flow verification |
| `t2-config-loader` | tier2 | repo | `pytest` |
| `t2-log-analyzer-cli` | tier2 | coding | exact JSON output |
| `t2-node-search-patch` | tier2 | repo | `node --test` |
| `t3-data-pipeline-report` | tier3 | multi_tool | exact report output |
| `t3-debug-timezone-regression` | tier3 | repo | `pytest` |
| `t3-feature-export` | tier3 | repo | `pytest` + CLI smoke |
| `t3-monitoring-automation` | tier3 | tools | script output + cron state |
| `t3-node-multifile-refactor` | tier3 | repo | `node --test` |
| `t4-browser-research-and-code` | tier4 | browser | browser evidence + tests |
| `t4-cross-repo-migration` | tier4 | repo | both test suites pass |
| `t4-delegation-repair` | tier4 | multi_tool | final suite + delegation transcript evidence |
| `t4-memory-recall-continuation` | tier4 | multi_tool | tests + memory assertions |
| `t5-contradictory-requirements` | tier5 | adversarial | latest-instruction artifact checks |
| `t5-hallucination-resistant-evidence` | tier5 | adversarial | exact answer + evidence-first checks |
| `t5-impossible-graceful-fail` | tier5 | adversarial | no harmful mutation + clear refusal |

## Query coverage layer

The benchmark also carries dataset-backed metadata from a spreadsheet-derived query corpus:

- scenario-domain mapping,
- clear vs ambiguous prompt slices,
- pass / partial / fail delivery buckets,
- weighted query-score reporting.

This lets the benchmark report both:

- how strong a model is,
- and what parts of the user-query landscape the suite is actually stressing.

## What makes ClawBench meaningful now

- execution-based completion checks instead of file-exists-only scoring
- property-based trajectory scoring instead of reference-trace matching
- deterministic local browser tasks instead of internet targets
- repeated-run reliability instead of one-shot success stories
- tiered tasks with delegation, memory, browser, repo, and adversarial surfaces
- advisory judge support without making the official score depend on a second model

## Auth model

The benchmark does not require a separate scorer or user-simulation API key.

It uses the model-under-test auth already configured for OpenClaw. If you enable the optional advisory judge, that model can reuse the same general auth path if available.
