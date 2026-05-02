# Meeting Brief: Nvidia, Hugging Face, vLLM

Meeting date: April 24, 2026

## One-Liner

ClawBench is a rigorous agent benchmark for measuring whether a model plus a
general harness plus plugins can cover the task domains served by most agent
SaaS products.

## What I Built

- A deterministic, trace-based benchmark for agents, not just models.
- A small public Core v1 set for reproducibility and regression tracking.
- A larger domain-suite scaffold for CRM, support, docs/sheets/slides, email,
  calendar, finance ops, analytics, security admin, ecommerce, devtools,
  research, and personal ops.
- A scoring system that separates completion, process quality, behavior,
  semantic quality, reliability, latency, tokens, cost, and failure modes.
- A dynamics-analysis stack that explains how agents fail: trapped, diffusive,
  convergent, chaotic, limit-cycle, and survival curves.
- A plugin-profile diagnostic layer that fingerprints configurations, estimates
  plugin contribution, detects dead-weight plugins, and recommends changes.
- An adapter boundary so OpenClaw can become one harness among several rather
  than the only execution path.

## Goal

Prove, with reproducible data, that specialized agent SaaS can be decomposed
into:

1. a base model,
2. a general agent harness,
3. a plugin stack,
4. domain-specific state/API access,
5. deterministic evaluation contracts.

If the data supports it, the conclusion is that the open plugin ecosystem can
subsume a large share of agent SaaS workflows.

## What The 19 Public Tasks Are

The 19 public tasks are not the whole proof. They are the public Core v1 set:

- reproducibility baseline
- CI/regression suite
- adapter bring-up set
- public explanation of methodology

The proof corpus is the domain suite. That needs more tasks, private variants,
and ablations.

## What Still Needs Help

- Cross-harness execution: OpenClaw is executable today; Hermes/Codex/Claude
  Code need end-to-end adapter wiring.
- Plugin provenance: tool calls need stable plugin owner IDs and registration
  traces.
- Domain corpus: each domain needs realistic private variants and hardened
  deterministic verifiers.
- Serving reproducibility: open-weight models need pinned serving recipes,
  GPU profiles, usage accounting, and latency/cost measurement.
- Scale: the domain ablations need a lot more runs than the public Core set.

## What I Want From Nvidia

- GPU-backed evaluation capacity for repeated domain sweeps.
- Profiling help: latency/pass, tokens/sec, cost/pass, memory pressure, and
  concurrency behavior for long agent trajectories.
- Reference serving profiles for open-weight models on NVIDIA hardware.
- Advice on making the benchmark useful for enterprise agent deployment, not
  just academic ranking.

## What I Want From Hugging Face

- Dataset hosting for public results, cached run JSON, and public task metadata.
- Private/controlled dataset workflow for holdout variants and partner traces.
- Model hosting paths for open-weight baseline runs.
- Help making ClawBench results easy to browse, reproduce, and cite.

## What I Want From vLLM

- A stable serving recipe for agent-eval workloads with long context and many
  tool turns.
- Usage accounting: prompt, output, reasoning/cache tokens where available.
- Throughput and latency guidance for many parallel agent runs.
- Integration advice for making model snapshots and serving configs auditable.

## Proposed Collaboration

1. Run Core v1 as a public sanity check across agreed open and closed models.
2. Build 12-domain private proof suite from `tasks-domain/`.
3. Run four ablation classes: model only, model plus harness, core plugins,
   domain plugins.
4. Publish aggregated domain coverage, reliability, failure modes, and cost.
5. Iterate on gaps where specialized SaaS still beats the open stack.

## The Ask

Help make the proof hard to dismiss:

- enough compute to run repetitions,
- clean serving recipes,
- model and dataset hosting,
- infrastructure review,
- partner traces or realistic domain workflows,
- public artifacts that other teams can reproduce.

