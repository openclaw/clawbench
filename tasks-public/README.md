# ClawBench Core v1 — Public Task Set (19 tasks)

A curated 19-task subset of the full ClawBench v0.4.0.dev1 dev pool,
selected for ranking consistency and capability coverage.

## What this is

19 tasks, 3 runs each → 57 runs per model. About half the compute of
the full 40-task sweep, with no loss of discriminative power on the
measured 8-model panel.

Derived from the v2026-4-19-full sweep archive by greedy task
selection: iteratively drop tasks that either (a) introduce ranking
inversions vs the reference ordering or (b) have near-zero cross-model
SNR and add only noise.

## Selection criteria

The 19-task subset was chosen so that, on the v2026-4-19-full archive
of 8 frontier models:

- The mean ranking has **0 inversions** vs the established 8-model order.
- The min adjacent-rank gap is **0.0049** — well above the ~0.002
  seed-noise floor estimated from inter-run variance.
- All 5 tiers and 6 task families remain represented.

Specific reference scores intentionally omitted from this README; they
are version-, provider-, and infra-dependent and would mislead anyone
reading them as a stable comparison number. Run the bench yourself
against your own configuration.

## Coverage

| Dimension | Breakdown |
|---|---|
| Tiers | T1=2, T2=6, T3=5, T4=5, T5=1 |
| Families | tools=8, coding=2, repo=3, browser=2, multi_tool=3, adversarial=1 |
| Capabilities | bugfix, test_authoring, multifile_reasoning, browser_debugging, structured_output, graceful_refusal, delegation, tool_composition, research_synthesis, cross_repo_change, memory_continuation |

## Directory layout

```
tasks-public/
├── MANIFEST.yaml          # Machine-readable task list + metadata
├── README.md              # This file
├── tier1/                 # 2 task YAMLs
├── tier2/                 # 6 task YAMLs
├── tier3/                 # 5 task YAMLs
├── tier4/                 # 5 task YAMLs
├── tier5/                 # 1 task YAML
└── assets/                # 19 asset packs (verifier scripts + fixtures)
```

## Build the Docker image

```bash
docker build -t clawbench .
```

The repo `Dockerfile` layers ClawBench on the configured OpenClaw base
image. Platform upgrades can shift scores, so record the OpenClaw
version for every published comparison and build both sides of a
comparison against the same OpenClaw release.

## How to run Core v1

Using the ClawBench harness:

```bash
# Explicit task-by-task (pass -t for each of 19 tasks):
clawbench run \
  --model anthropic/claude-opus-4-6 \
  --runs 3 \
  --concurrency 4 \
  --profile profiles/frontier_opus_4_6.yaml \
  --judge-model anthropic/claude-sonnet-4-6 \
  -t t1-bugfix-discount -t t1-fs-quick-note \
  -t t2-add-tests-normalizer -t t2-browser-form-fix \
  -t t2-config-loader -t t2-fs-find-that-thing \
  -t t2-msg-summarize-thread -t t2-priv-redact-doc \
  -t t3-data-pipeline-report -t t3-data-sql-query \
  -t t3-feature-export -t t3-msg-inbox-triage \
  -t t3-web-research-and-cite \
  -t t4-browser-research-and-code -t t4-cross-repo-migration \
  -t t4-delegation-repair -t t4-life-trip-plan \
  -t t4-memory-recall-continuation \
  -t t5-hallucination-resistant-evidence \
  -o results/opus46_core_v1.json
```

Or point the harness at this directory by setting the task root in
your ClawBench config. See MANIFEST.yaml for a programmatic list.

## Reproducibility caveats

- **Exact score reproduction is not guaranteed.** Even with the same
  OpenClaw version, re-runs exhibit seed noise (~0.02 stddev per task,
  per model). Rankings are stable; absolute scores drift within that
  envelope.
- **OpenRouter-routed models** (`openrouter/*`) can have their
  scores shift if OpenRouter repoints its model slug to a different
  underlying provider. We observed this with GLM 5.1 between
  2026-04-20 14:00 and 17:00 PST. Pin to canonical model versions
  (e.g. `z-ai/glm-5-turbo-20260315`) for stable measurement.
- **OpenClaw platform version matters.** Upgrading from 4.9 → 4.15-beta.1
  shifted scores by +0.13 to +0.29 across models. Build both sides of
  any comparison from the same OpenClaw release.
- **Judge scores** are advisory and depend on the configured judge model.
  They are reported separately and cannot replace deterministic checks.

## What's NOT in Core v1

21 tasks from the full dev pool are held back:
- **9 ceiling tasks** (all frontier models score >0.85) — don't
  discriminate, future releases may phase them out.
- **9 noise tasks** (cross-model SNR < 0.5) — either broken verifiers
  or genuinely ambiguous prompts. Scheduled for redesign.
- **3 ranking-breaker tasks** — tasks where the cross-model ordering
  conflicts with the reference ranking. Not broken per se; just
  inconsistent with the headline. Their task IDs and contents remain
  private with the rest of the holdout.

Also missing entirely from Core v1:
- **Tier 6 long-horizon (100+ turn) tasks** — planned for v2.
- **Creative synthesis / style-matching tasks** — planned for v2.
- **Paraphrased prompt pairs** for perturbation-sensitivity
  measurement — planned for v2.

## Versioning

| Version | Tasks | Change |
|:---:|:---:|---|
| Core v1 | 19 | Initial public release (this) |
| Core v2 | ~24 | Planned: +Tier 6, +paraphrase pairs, -2 noise tasks |

Pin to `clawbench-core-v1` in the MANIFEST for reproducible
comparison across releases.
