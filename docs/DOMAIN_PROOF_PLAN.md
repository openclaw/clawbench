# ClawBench Domain Proof Plan

This plan turns ClawBench from a strong benchmark into an evidence package for
the central thesis:

> Model + general harness + plugins can cover the task domains served by most
> agent SaaS products.

## What Exists Now

- `tasks-public/`: small public Core v1 task set for reproducibility,
  examples, and regression tracking.
- `tasks-domain/`: domain coverage scaffold for the larger proof corpus.
- Deterministic scoring: file, execution, memory, session, cron, gateway, DOM,
  and structured output assertions.
- Process scoring: read-before-write, self-verification, recovery, safety,
  tool-family fit.
- Reliability scoring: repeated runs, pass^k, worst-of-n, variance score,
  bootstrap confidence intervals.
- Dynamics analysis: regime classification, survival, constraint index,
  variance decomposition, SNR-weighted ranking.
- Configuration diagnostics: plugin profile fingerprints, utilization audit,
  manifest-vs-reality gap, surprise detection, recommendations.
- Adapter groundwork: canonical task schema plus OpenClaw and Hermes adapter
  modules. OpenClaw is the executable harness path today.

## Ablation Design

Each domain task should run under four configuration classes.

| Class | Description | Question Answered |
|---|---|---|
| `model_only` | Model with minimal shell/filesystem access | What can the raw model do with little scaffolding? |
| `model_plus_harness` | Model plus the general OpenClaw-style harness | What does the harness contribute by itself? |
| `core_plugins` | Harness plus browser, memory, filesystem, execution plugins | What do common plugins add across domains? |
| `domain_plugins` | Harness plus domain-specific state/API plugins | Does the plugin stack close the gap to specialized SaaS agents? |

Run policy:

- 3 runs per task per configuration class
- same model snapshots across all classes
- same OpenClaw/harness build across all classes
- same private task variants across all classes
- fixed time, token, tool, and approval budgets

## Primary Metrics

- hard success: deterministic completion only
- reliability: pass^k, pass rate, worst-of-n, variance score
- process quality: trace-derived behavior quality
- cost efficiency: tokens/pass, cost/pass, p50/p95 latency
- failure profile: 13 deterministic failure modes
- plugin lift: `domain_plugins - model_plus_harness`
- harness lift: `model_plus_harness - model_only`
- plugin utilization: loaded vs invoked, tool-family coverage
- manifest-reality gap: claimed plugin capabilities vs observed use

## Proof Criteria

A domain is considered covered when:

- `domain_plugins` reaches at least 0.85 hard success on private variants
- pass^k is at least 0.75 across 3 runs
- worst-of-n is at least 0.65
- no dominant failure mode accounts for more than 35 percent of failures
- plugin utilization shows the relevant domain plugin was invoked on tasks
  where it was required

The broader thesis is credible when:

- at least 10 of 12 domains meet the domain coverage bar
- plugin lift is larger than model-to-model variance on the same task set
- holdout variants preserve the same conclusions
- SNR analysis shows the ranking is signal-dominant, not seed-noise-dominant
- cross-harness adapters reproduce scores within an agreed tolerance

## Workstream 1: Adapter Execution

Goal: make OpenClaw, Hermes, Codex, and Claude Code comparable through one
canonical task pipeline.

Near-term:

- keep `--adapter openclaw` as the executable path
- route OpenClaw through the adapter implementation instead of inline gateway
  code
- add compatibility reporting for every task and adapter
- implement Codex and Claude Code transcript adapters
- promote Hermes from first-turn runner to full compatible runner where possible

Help wanted:

- harness owners: SDK or CLI entry points that expose full transcripts
- plugin owners: tool-call provenance and registration traces
- serving owners: stable model IDs, usage accounting, and reproducible configs

## Workstream 2: Plugin Provenance

Goal: attribute score changes to plugins instead of treating the agent as a
black box.

Near-term:

- capture plugin registration traces at gateway startup
- attach plugin owner IDs to every tool call
- store transcripts and plugin traces alongside result JSON
- include utilization and manifest-reality gaps in every `--profile` run

Help wanted:

- OpenClaw plugin registry hooks for runtime trace export
- partner plugins with typed manifests and clean provenance
- ClawHub metadata sync for manifest cache refresh

## Workstream 3: Domain Corpus

Goal: replace a small public task suite with a coverage matrix for real agent
SaaS domains.

Near-term:

- 12 domains in `tasks-domain/MANIFEST.yaml`
- 5 templates per domain
- 3 private variants per template
- domain-specific plugin requirement declarations
- deterministic verifier contracts before any semantic judge

Help wanted:

- partner traces that can be transformed into private variants
- domain experts to validate task realism and verifier quality
- infra for private variant generation and contamination audits

## Workstream 4: Serving and Cost Rigor

Goal: compare open and closed models under reproducible serving constraints.

Near-term:

- record model snapshot, provider, serving stack, quantization, GPU class,
  context length, temperature, reasoning settings, and token accounting
- report cost/pass and latency/pass alongside capability
- run open-weight models through vLLM-backed profiles where available

Help wanted:

- vLLM serving recipes for consistent agent-eval runs
- Hugging Face model hosting and dataset plumbing
- NVIDIA profiling on representative GPU setups

## Workstream 5: Evidence Package

Goal: make the conclusion auditable by third parties.

Near-term:

- publish public Core v1 results as the reproducibility baseline
- publish domain coverage matrix without private task bodies
- publish aggregated per-domain scores, confidence intervals, and failure modes
- keep private variants for contamination-resistant official scoring
- publish scripts that regenerate every report from cached run JSON

Help wanted:

- compute credits for multi-model sweeps
- review from model serving, benchmark, and infrastructure teams
- public hosting for result artifacts and visual dashboards

