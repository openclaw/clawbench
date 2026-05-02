# ClawBench v0.4 Spec

## Purpose

ClawBench v0.4 upgrades ClawBench from a strong OpenClaw-native coding benchmark into a more robust frontier-agent evaluation system.

The design goals are:

1. Keep deterministic outcome verification as the primary source of truth.
2. Increase model differentiation without rewarding style over substance.
3. Separate capability, reliability, efficiency, and semantic quality instead of collapsing them too early into one scalar.
4. Reduce overfitting pressure with public dev tasks, hidden official tasks, and rotating variants.
5. Prepare the benchmark for eventual cross-harness evaluation through a canonical task schema and adapter boundary.

## Non-Goals

- Replacing execution-based grading with LLM judging.
- Turning every task into an open-ended knowledge-work artifact task.
- Shipping a cross-harness benchmark in a single release.

## Principles

- Deterministic first: if a task can be verified by execution, hidden tests, trace checks, or structural comparison, do that.
- Judges only for irreducibly semantic residue.
- Public methodology, partially private tasks.
- Multiple score surfaces instead of one opaque leaderboard number.
- Reliability matters as much as one-shot brilliance.
- Failure modes are benchmark outputs, not just debugging metadata.

## Benchmark Structure

ClawBench v0.4 has three benchmark layers.

### Layer A: Core Deterministic Evaluation

This is the primary leaderboard layer.

- Binary task pass/fail from deterministic verification.
- Partial credit from deterministic sub-assertions only.
- Applies to coding, browser, cron, delegation, memory, and adversarial tasks.

### Layer B: Process and Robustness Evaluation

This measures whether the agent worked in a way we actually want.

- Read-before-write
- Verification-after-mutation
- Recovery after failures
- Tool-family fit
- Unsafe behavior penalties
- Failure taxonomy

This remains secondary to hard completion, but it is still first-class and reported separately.

### Layer C: Semantic Quality Evaluation

This is restricted to tasks where execution cannot fully capture quality.

Examples:

- Architecture briefs
- Incident summaries
- Evidence-backed research memos
- Code review writeups

Semantic quality never rescues failed completion.

## Task Pools

Tasks are split into four pools.

### `public_dev`

- Public
- Stable
- Used for local debugging, examples, and adapter bring-up
- Not used for official leaderboard ranking

### `official_hidden`

- Private task bodies and/or hidden variants
- Used for official leaderboard scoring
- Rotated periodically

### `consensus`

- Highly audited subset with extremely trustworthy verification
- Lower ambiguity, lower noise
- Used for regression tracking and judge calibration

### `hard`

- Frontier-separating subset
- Keeps headroom as models improve
- Preferred public-facing score for serious comparisons

## Task Taxonomy

Current tiering stays, but v0.4 adds explicit capability tags.

Each task will declare:

- `tier`
- `family`
- `surface`
- `capabilities`
- `category`
- `domain`
- `functionality`
- `trace_distribution`
- `tool_surface`
- `risk_tags`
- `pool`
- `variant_group`
- `official`
- `semantic_judge`

The added dimensions are flat, orthogonal leaderboard axes. They are not
sublevels of tier or scenario, and they must not encode a specific agent
product. The result schema aggregates scores by each axis so OpenClaw,
Hermes, plugin-backed runs, and other third-party harnesses can compare
the same verifier set by task mix without rewarding a harness-specific
setup.

Recommended capability tags:

- `bugfix`
- `refactor`
- `test_authoring`
- `multifile_reasoning`
- `browser_debugging`
- `structured_output`
- `memory_continuation`
- `delegation`
- `tool_composition`
- `research_synthesis`
- `graceful_refusal`
- `spec_revision`
- `cross_repo_change`
- `automation`

## Official Run Policy

- Official scores require at least 3 runs per task.
- Report `pass@1`, pass rate, `pass^k`, variance, and worst-of-n reliability.
- Official runs must use fixed benchmark-owned time, tool, and token budgets.
- Public dev runs may be single-run and lower budget.

## Verification Policy

### Allowed Primary Verifiers

- Hidden unit and integration tests
- Deterministic verifier scripts
- Structured file assertions
- Exact JSON or normalized JSON comparison
- Expected stdout or structured output comparison
- DOM assertions
- Network-trace assertions
- Memory state assertions
- Cron state assertions
- Gateway state assertions

### Disallowed Primary Verifiers

- File existence alone for coding tasks
- Transcript-only “seems done” checks
- Holistic LLM judging when deterministic verification is possible

### Verification Tiers

Each task verifier should be classified as one of:

- `exact`
- `normalized_structural`
- `state_transition`
- `execution`
- `trace_based`
- `semantic`

`semantic` should be the exception, not the default.

## Browser Verification

Browser tasks should move closer to WebArena-Verified style evaluation.

Required additions:

- Capture browser/network traces per run.
- Allow verifiers to assert requests, methods, status codes, routes, parameters, and final DOM state.
- Prefer network and state assertions over fragile text matching when possible.
- Preserve local deterministic services only; no live public sites in official runs.

## Failure Taxonomy

Every failed run should be assigned a primary failure mode.

Initial taxonomy:

- `graceful_refusal`
- `hallucinated_completion`
- `repeated_error_loop`
- `tool_misuse`
- `unsafe_mutation`
- `verification_skipped`
- `state_regression`
- `memory_miss`
- `delegation_failed`
- `browser_navigation_failure`
- `environment_unavailable`
- `timeout`
- `reward_hack_suspected`

This should be generated deterministically where possible, with a manual review path for suspicious runs.

## Efficiency Metrics

ClawBench v0.4 should report:

- `median_latency_ms`
- `p95_latency_ms`
- `input_tokens`
- `output_tokens`
- `reasoning_tokens` if available
- `total_tokens`
- `estimated_cost_usd`
- `cost_per_pass`
- `tokens_per_pass`

These should appear beside capability metrics, not buried in raw logs.

## Reliability Metrics

We keep run repetition but expand reporting.

Per task:

- `pass@1`
- `pass_rate`
- `pass^k`
- `worst_of_n`
- `stddev`
- `variance_score`

Per benchmark:

- mean over tasks for the above
- by-tier and by-capability breakdowns

Worst-of-n should be especially visible for adversarial and safety-sensitive tasks.

## Scoring Model

v0.4 should stop over-compressing everything into one number.

### Primary Surfaces

- `HardSuccess`
- `ProcessQuality`
- `Reliability`
- `Efficiency`
- `FailureProfile`
- `SemanticQuality` where applicable

### Suggested Presentation

Primary leaderboard columns:

- `HardSuccess`
- `Reliability`
- `Median Latency`
- `Cost per Pass`
- `Hard subset`
- `Consensus subset`

Secondary detail panels:

- Tier breakdown
- Capability breakdown
- Failure-mode histogram
- Process-quality metrics
- Semantic-quality metrics

### Single Composite Score

If a single score is required for ranking, use it as a display convenience only.

Recommended composite:

`official_score = 0.6 * hard_success + 0.2 * reliability + 0.1 * process_quality + 0.1 * efficiency_score`

Constraints:

- If deterministic completion fails, semantic quality does not contribute.
- If a run is flagged `unsafe_mutation` or `reward_hack_suspected`, the run score is zeroed or quarantined.
- Composite score should not be the only published output.

## Semantic Judge Policy

Semantic judges are allowed only for tasks with `semantic_judge.enabled = true`.

### Where To Use Judges

- architecture brief quality
- evidence memo quality
- incident report quality
- code review quality
- research synthesis quality

### Where Not To Use Judges

- bugfix correctness
- browser success
- cron state
- file generation correctness
- exact-output CLI tasks
- test-authoring correctness

### Judge Design

- Prefer 3 judges from different families.
- Blind to model and harness identity.
- Rubric-based scoring only.
- Score per criterion, not one global impression.
- Randomize candidate ordering.
- Log criterion-level decisions for auditability.
- Cap semantic contribution to a minority share of the task score.

### Judge Gating

Semantic judging only runs if:

- the deterministic completion floor is met, or
- the task family is explicitly semantic-first

## Anti-Gaming Design

ClawBench v0.4 should adopt:

- public dev set + hidden official set
- hidden variants within task families
- periodic rotation
- canary strings in private tasks
- verifier hardening reviews
- suspicious-run review pipeline

For private tasks, do not expose exact hidden assertions in public result payloads.

## Canonical Task Schema

v0.4 should start separating canonical task intent from OpenClaw execution details.

### Canonical Layer

- task metadata
- goal
- assets
- verifier contract
- budgets
- interaction policy
- expected deliverable types

### OpenClaw Adapter Layer

- user turn generation
- tool-family normalization
- session and workspace lifecycle
- browser connection details
- memory and cron integration

This lets ClawBench remain OpenClaw-first now while making future cross-harness support feasible.

## Codebase Changes

### `clawbench/schemas.py`

Add:

- `TaskPool`
- `CapabilityTag`
- `VerifierKind`
- `FailureMode`
- `EfficiencyResult`
- `SemanticJudgeConfig`
- `TaskVariant`

Extend `TaskDefinition` with:

- `capabilities`
- `pool`
- `variant_group`
- `official`
- `semantic_judge`

### `clawbench/environment.py`

Add:

- verifier classification metadata
- trace-based verifier support
- normalized JSON / structured diff helpers
- failure-mode hints from verifier outcomes

### `clawbench/trajectory.py`

Add:

- explicit verification-gap detection
- reward-hack suspicion heuristics
- richer browser trace scoring hooks
- deterministic failure-mode inference from traces

### `clawbench/scorer.py`

Refactor to output:

- `hard_success`
- `process_quality`
- `semantic_quality`
- `efficiency_score`
- `failure_mode`

Completion remains the primary gate.

### `clawbench/stats.py`

Add aggregation for:

- worst-of-n
- cost and token efficiency
- per-capability breakdowns
- public-dev vs official-hidden splits
- consensus vs hard subset reporting

### `clawbench/harness.py`

Add:

- public/hidden pool selection
- hidden variant resolution
- token/cost logging
- trace capture support
- suspicious-run quarantine hooks

### `clawbench/client.py`

Add:

- normalized usage extraction if the gateway exposes token and cost data
- browser trace export hooks where supported

### `clawbench/tasks.py`

Add:

- pool filtering
- capability filtering
- variant expansion
- hidden-manifest loading

### `app.py` and `clawbench/render.py`

Update UI to show:

- hard success
- reliability
- efficiency
- failure profile
- consensus / hard subset

## Task Roadmap

### Keep and Harden

- browser repair
- memory continuation
- delegation repair
- contradictory requirements
- hallucination-resistant evidence

### Add in v0.4

- hidden regression bugfix families
- browser tasks with network-trace verification
- tool-composition tasks with no obvious single-tool route
- ambiguity-resolution tasks with structured intermediate evidence
- semantic tasks with deterministic floor + judge overlay

### Remove or Rewrite

Rewrite any task whose success is still dominated by:

- brittle substring checks
- transcript cosmetics
- public fixture memorization
- one obvious happy-path implementation

## Phased Implementation

### Phase 1

- Add pools, variants, capabilities, failure taxonomy, and efficiency logging.
- Add consensus and hard subsets.
- Keep deterministic-only official scoring.

### Phase 2

- Add trace-based browser verification.
- Add worst-of-n reliability reporting.
- Add suspicious-run audit pipeline.

### Phase 3

- Add semantic judge support for a small semantic subset.
- Calibrate judges against human labels on consensus tasks.

### Phase 4

- Introduce canonical task format and OpenClaw adapter boundary.
- Prepare for cross-harness evaluation.

## Success Criteria for v0.4

ClawBench v0.4 is successful if:

1. Frontier models separate more clearly on hard and reliability metrics than in v0.3.
2. Shared failures are attributable to capability gaps more often than verifier brittleness.
3. Official benchmark results remain reproducible across reruns.
4. Judge-based scores, where used, correlate well with human labels on calibration tasks.
5. Overfitting pressure is reduced by hidden variants and rotation.

---

# v0.5 Direction: Configuration-Space Benchmarking

## Motivation

Every existing agent benchmark — OSWorld, SWE-bench, WebArena, GAIA — treats the agent as a black box and the model as the variable. Recent evidence inverts this assumption: on SWE-bench Pro, swapping scaffolds produces 22+ point swings while swapping frontier models produces ~1 point swings. The same Claude Sonnet beats Claude Opus when wrapped in better tooling. The configuration is the product, not the model.

OpenClaw's plugin-native architecture makes ClawBench uniquely positioned to exploit this. Because everything in OpenClaw is a plugin with a typed manifest, the benchmark can look *inside* a configuration in a way that no opaque-agent benchmark can. ClawBench v0.5 turns this structural visibility into its primary differentiator.

## Position

ClawBench v0.5 is not a leaderboard for agents. It is a **diagnostic benchmark for plugin configurations** in an open ecosystem. Users submit a plugin profile (bundled plugins + ClawHub installs + custom plugins). The benchmark runs the profile, decomposes which plugins contributed what, and recommends specific changes — all grounded in the plugin manifest contracts that OpenClaw already requires.

This is a structurally novel position because:

- The configuration space is **open-ended** (any third party can publish plugins or build custom ones), so closed-set ablation is impossible.
- Plugin manifests provide a **typed feature space** for any plugin — bundled, ClawHub, or custom — enabling generalization to unseen configurations.
- Plugin hooks create **emergent interactions** (planning hook + tool-approval hook → planned-then-cautious behavior) that no individual plugin's manifest predicts.

No other benchmark has these three properties together because no other benchmark is plugin-native.

## Non-Goals for v0.5

- Replacing the deterministic execution-based scoring of v0.4. The configuration analysis sits *on top of* the v0.4 verifier, not in place of it.
- Closed-set comparison of a fixed list of plugins. The framework must handle plugins it has never seen.
- Trained reward models or LLM judges for configuration scoring. All factor decomposition uses execution-verified ground truth.
- Building a plugin marketplace. ClawHub already exists. ClawBench consumes its metadata, it does not duplicate it.

## Core Concepts

### Plugin Profile

A submission is a Plugin Profile: the full set of plugins enabled for a benchmark run, plus their per-plugin config and slot assignments.

```yaml
profile:
  name: "research-assistant-v3"
  base_model: "claude-sonnet-4"
  plugins:
    enabled:
      - id: "anthropic"
      - id: "memory-lancedb"
        config: { dimensions: 1536 }
      - id: "browser-playwright"
      - id: "github"
      - id: "clawhub:rag-pinecone@1.2.0"   # ClawHub plugin
      - id: "local:./my-code-reviewer"      # Custom plugin
    slots:
      memory: "memory-lancedb"
      contextEngine: "builtin"
    tools_allow: ["bash", "file_read", "file_edit", "browser_navigate", ...]
```

Two profiles are considered the same configuration if and only if their fingerprints (defined below) match. Profiles are the unit of benchmarking, ranking, and comparison.

### Manifest Feature Vector

Every plugin — known or unknown — has a feature vector derived mechanically from its `openclaw.plugin.json` manifest and (after loading) its registration trace. The vector is the same shape for every plugin so the framework generalizes.

```python
def plugin_features(manifest, registration_trace):
    return {
        # Contract declarations (from manifest, no code execution required)
        "provides_tools_count":         len(manifest.contracts.tools or []),
        "provides_memory":              "memory" in (manifest.kind or []),
        "provides_context_engine":      "context-engine" in (manifest.kind or []),
        "provides_web_search":          bool(manifest.contracts.webSearchProviders),
        "provides_web_fetch":           bool(manifest.contracts.webFetchProviders),
        "provides_speech":              bool(manifest.contracts.speechProviders),
        "provides_image_generation":    bool(manifest.contracts.imageGenerationProviders),
        "provides_video_generation":    bool(manifest.contracts.videoGenerationProviders),
        "provides_media_understanding": bool(manifest.contracts.mediaUnderstandingProviders),
        "provides_memory_embedding":    bool(manifest.contracts.memoryEmbeddingProviders),
        "n_channels":                   len(manifest.channels or []),
        "n_providers":                  len(manifest.providers or []),
        "clawhub_capability_tags":      manifest.capabilityTags or [],
        "clawhub_channel":              manifest.clawhub_channel or "bundled",
        "clawhub_is_official":          manifest.clawhub_is_official,

        # Hook footprint (from registration trace)
        "hooks_before_agent_start":     "before_agent_start" in trace.hooks,
        "hooks_before_prompt_build":    "before_prompt_build" in trace.hooks,
        "hooks_before_tool_call":       "before_tool_call" in trace.hooks,
        "hooks_after_tool_call":        "after_tool_call" in trace.hooks,
        "hooks_llm_input":              "llm_input" in trace.hooks,
        "hooks_llm_output":             "llm_output" in trace.hooks,
        "hooks_before_compaction":      "before_compaction" in trace.hooks,
        "hooks_message_sending":        "message_sending" in trace.hooks,
        "hooks_subagent_spawning":      "subagent_spawning" in trace.hooks,
        # ... one column per hook in the 25-hook surface

        # Tool-level features (classified from registered tools)
        "tool_families":                classify_tool_families(trace.tools),
        # → multi-hot over {browser, file, search, execute, memory, delegate, ...}

        # Surface area
        "registers_gateway_methods":    bool(trace.gatewayMethods),
        "registers_http_routes":        bool(trace.httpRoutes),
        "registers_services":           bool(trace.services),
    }
```

The critical property: this function is **defined entirely over the plugin SDK contract**. Any plugin that loads in OpenClaw produces a valid feature vector. No hand-curation per plugin. No allowlist of known plugins.

### Profile Fingerprint

A Profile Fingerprint is the aggregation of all plugin feature vectors in a profile, plus profile-level features (slot assignments, tool allowlist, base model). It is the structural summary used for similarity search and prediction.

```python
def profile_fingerprint(profile):
    plugin_vectors = [plugin_features(p) for p in profile.plugins]
    return {
        # Aggregated capability coverage (union over plugins)
        "capability_coverage": union(v.contract_capabilities for v in plugin_vectors),
        # Aggregated hook footprint
        "hook_footprint":      union(v.hooks_active for v in plugin_vectors),
        # Aggregated tool family surface
        "tool_family_surface": union(v.tool_families for v in plugin_vectors),
        # Slot fills
        "memory_slot":         profile.slots.memory or "none",
        "context_engine_slot": profile.slots.contextEngine or "none",
        # Counts and provenance
        "n_plugins":           len(profile.plugins),
        "n_clawhub_plugins":   count(p for p in profile.plugins if p.source == "clawhub"),
        "n_custom_plugins":    count(p for p in profile.plugins if p.source == "local"),
        # Base model is part of the configuration
        "base_model":          profile.base_model,
    }
```

Two profiles with identical fingerprints should score similarly. Two profiles with similar fingerprints should score similarly. This is the assumption that makes prediction tractable in an open ecosystem.

## The Three-Layer Framework

### Layer 1: Manifest Fingerprinting (zero runs, instant)

Compute the profile fingerprint from the plugin manifests alone. This requires no benchmark runs and produces:

- A structural summary of what the configuration *should* be capable of
- A quick sanity check (does the profile fill the slots it needs for the tasks it will face?)
- Input to the Layer 2 prediction engine

This is the cheapest layer and runs on every submission, including dry-run validation before paying for benchmark execution.

### Layer 2: Similarity-Based Prediction (from accumulated data)

After N ≥ 30 historical submissions exist, ClawBench can predict the score of a new profile before running it.

```python
def predict_profile_score(new_profile, historical_data):
    fingerprint = profile_fingerprint(new_profile)
    neighbors = k_nearest_neighbors(
        fingerprint,
        historical_data,
        k=10,
        metric=fingerprint_similarity,  # Jaccard on capability/hook/tool sets
    )
    predicted_overall = weighted_mean(
        [n.actual_score for n in neighbors],
        weights=[1.0 / (n.distance + epsilon) for n in neighbors],
    )
    predicted_per_task = {
        task_id: weighted_mean(
            [n.actual_score_per_task[task_id] for n in neighbors if task_id in n.actual_score_per_task],
            weights=[1.0 / (n.distance + epsilon) for n in neighbors if task_id in n.actual_score_per_task],
        )
        for task_id in all_task_ids
    }
    capability_attributions = compute_marginal_attribution(
        fingerprint, historical_data
    )
    return PredictionReport(
        predicted_score=predicted_overall,
        confidence=confidence_from_neighbor_density(neighbors),
        per_task=predicted_per_task,
        attributions=capability_attributions,
        nearest_profiles=[n.profile_name for n in neighbors],
    )
```

The output is a **before-running** estimate plus a confidence band derived from neighbor density. Profiles in well-explored regions of the fingerprint space get tight predictions; profiles with novel plugin combinations get wide predictions and are flagged as "exploration".

### Layer 3: Empirical Validation and Surprise Detection (after runs)

After actually running the benchmark, compare prediction to reality.

```python
def analyze_run(profile, prediction, actual):
    overall_error = actual.score - prediction.predicted_score
    surprises = []
    for task_id, predicted_score in prediction.per_task.items():
        actual_score = actual.per_task[task_id]
        delta = actual_score - predicted_score
        if abs(delta) > SURPRISE_THRESHOLD:
            surprises.append(Surprise(
                task_id=task_id,
                predicted=predicted_score,
                actual=actual_score,
                direction="positive" if delta > 0 else "negative",
                likely_cause=attribute_surprise(profile, task_id, delta),
            ))
    historical_data.append((profile.fingerprint, actual))
    if surprises:
        flag_for_community_insights(profile, surprises)
    return AnalysisReport(
        calibration_error=overall_error,
        surprises=surprises,
        updated_attributions=recompute_attributions_with_new_datapoint(),
    )
```

**Surprises are the highest-value output of the framework**, because they fall into three categories:

1. **Hidden utility**: a plugin performs better than its manifest predicts. This is a discovery event — the community should know.
2. **Manifest over-promise**: a plugin performs worse than its manifest predicts. This is a warning event — users should be cautioned.
3. **Emergent interaction**: a *combination* of plugins performs differently than the sum of their individual contributions. This is the gold standard finding — manifests cannot capture interactions, only empirical data can.

## Mathematical Tooling

The framework uses three established techniques, applied to a domain where they have not been used before. Each is included only because it answers a question that no simpler tool can.

### Functional ANOVA (fANOVA) for Factor Importance

**Question answered**: When a profile changes, which feature dimensions actually drive the score change?

Fit a Random Forest regressor `f: profile_features → score` over all submitted profiles. Apply functional ANOVA variance decomposition:

```
V(f) = Σᵢ Vᵢ + Σᵢ<ⱼ Vᵢⱼ + higher-order terms
importance(featureᵢ)        = Vᵢ / V(f)
interaction(featureᵢ, j)    = Vᵢⱼ / V(f)
```

`Vᵢ` is the variance of `f` attributable to feature `i` alone; `Vᵢⱼ` is the variance attributable to the interaction of features `i` and `j` after their main effects are removed.

**Why this and not simpler statistics**: univariate correlations cannot reveal interactions. fANOVA handles mixed categorical and continuous features natively (via the Random Forest surrogate). Optuna ships an `FanovaImportanceEvaluator` and the original `fanova` package is the reference implementation. This technique is standard in hyperparameter optimization and AutoML; it has never been applied to agent configurations.

### k-Nearest-Neighbor Similarity for Cold-Start Prediction

**Question answered**: For a never-before-seen plugin combination, what should we expect?

Use Jaccard similarity over the categorical components of the fingerprint (capability sets, hook sets, tool families) and Euclidean distance over the continuous components (counts). Combine into a composite distance and run weighted k-NN.

**Why this and not a deep model**: cold start. The framework must produce useful output after 30 submissions, not 30,000. k-NN with a well-engineered similarity metric is the right tool when data is scarce and structure is interpretable. It also gives free explainability — the prediction comes with the names of the neighboring profiles that produced it.

### Taguchi Signal-to-Noise for Robustness

**Question answered**: Which configurations are robust across task tiers, not just optimal on average?

For a profile with per-task scores `y₁, y₂, ..., yₙ`, compute the larger-is-better signal-to-noise ratio:

```
S/N = -10 × log₁₀( (1/n) × Σᵢ (1/yᵢ²) )
```

Rank profiles separately by mean score and by S/N ratio. Surface both in the leaderboard.

**Why this and not just stddev**: the S/N ratio is dominated by the worst-performing tasks (because of the 1/yᵢ² term), which is exactly the behavior practitioners care about. A configuration that scores 0.85 on average but 0.10 on adversarial tasks is *worse* in production than one that scores 0.78 average but never drops below 0.65. Taguchi's framework, designed for manufacturing quality control under noise, maps cleanly onto agent benchmarking under task-distribution variation.

## What v0.5 Cuts From Earlier Drafts

This spec deliberately excludes techniques that were considered and rejected as gimmicks:

- **Shapley value attribution over scoring dimensions**: redundant with fANOVA at this scale; the marginal interpretation does not improve on variance decomposition for a few hand-chosen dimensions.
- **Process Reward Models trained on trajectory data**: requires a labeled trajectory dataset that does not exist; the v0.4 deterministic verifier already provides a strong outcome signal.
- **Graph Structural Similarity Index over action DAGs**: requires hand-authored reference DAGs per task; high maintenance, low signal beyond what trajectory property checks already capture.
- **Information Gain Rate over trajectories**: elegant but requires mid-trajectory assertion checkpointing that the harness does not support yet. Deferred to a future trajectory-quality spec.
- **Bayesian adaptive run allocation**: valuable but secondary; ship fixed-N first, add adaptive stopping after enough data exists to fit IRT-like priors.
- **2PL IRT over models**: misaligned with the v0.5 framing; the unit of measurement here is the *configuration*, not the model. IRT can be revisited once the configuration framework is established and there is enough data for a configuration-vs-task IRT fit.

The exclusion principle: every technique in v0.5 must answer a question that no simpler tool can answer. Math for its own sake is rejected.

## Submission Flow

```
1. User authors profile.yaml
2. ClawBench validates manifest compatibility for all referenced plugins
3. Layer 1: compute fingerprint, run dry-run sanity checks
4. Layer 2: query historical data, produce pre-run prediction (if data available)
5. User confirms intent to spend benchmark compute
6. Harness runs all v0.4 tasks with the submitted profile
7. v0.4 deterministic scoring produces per-task and aggregate scores
8. Layer 3: compare prediction to reality, detect surprises, update model
9. Generate Configuration Diagnostic Report
10. Optionally: store fingerprint and results in shared historical data
```

## Configuration Diagnostic Report

The user-facing output of a submission is the Configuration Diagnostic Report, not just a leaderboard score. Required sections:

1. **Score and rank**: overall score, confidence interval, percentile in the population of submissions
2. **Pre-run prediction vs. actual**: did the framework predict correctly? Calibration matters and should be visible.
3. **Plugin utilization audit**: for each plugin in the profile, was it actually invoked during the run? Plugins that loaded but were never called are flagged as dead weight.
4. **Manifest vs. reality gap**: for each plugin, did it impact the tasks its manifest suggested it would? Discrepancies are listed.
5. **Surprise list**: tasks where actual score deviated from prediction by more than the surprise threshold, with a hypothesis for the cause.
6. **Capability attributions**: estimated marginal contribution of each capability dimension to the overall score.
7. **Robustness profile**: mean, S/N ratio, worst-of-n, distribution across tiers.
8. **Recommendations**: ordered list of suggested profile changes with estimated score impact.

The Recommendations section is the prescriptive output that distinguishes ClawBench from descriptive leaderboards. Every recommendation must be backed by data — either neighbor profiles that already include the suggested plugin, or attribution estimates with explicit confidence.

## Community Insights

After accumulated submissions, ClawBench publishes ecosystem-level insights derived from the historical fingerprint database:

- **Plugin impact leaderboard**: average score delta when each plugin is added to comparable profiles
- **Strongest interactions**: plugin pairs whose joint contribution exceeds the sum of their marginals
- **Overhyped plugins**: plugins with high install counts on ClawHub but low or negative measured impact
- **Underrated plugins**: plugins with low install counts but high measured impact
- **Capability gaps**: task families where no submitted plugin combination scores above a threshold

These insights are computed automatically from accumulated runs. Plugin authors get empirical evidence of their plugin's value. Agent builders get data-driven recommendations. ClawHub gets a feedback loop from real benchmark results.

## Data Model

### `submissions/`
- `<profile_hash>.json` — full submission record
  - `profile`: the submitted profile
  - `fingerprint`: computed Profile Fingerprint
  - `prediction`: pre-run prediction (if available)
  - `actual`: per-task and aggregate scores from v0.4 verifier
  - `analysis`: surprise list, calibration error, attributions
  - `metadata`: submitter, timestamp, openclaw version, clawbench version

### `historical/`
- `fingerprints.parquet` — flat table of `(fingerprint_features, task_id, score)` for fast similarity search and fANOVA fitting
- `plugin_manifests.parquet` — cached manifest features per plugin id, refreshed on ClawHub sync
- `neighbors_index/` — pre-built ANN index over fingerprints for fast k-NN queries

### `insights/`
- `factor_importance.json` — current fANOVA decomposition
- `plugin_leaderboard.json` — plugin impact ranking
- `interactions.json` — discovered plugin interactions
- `gaps.json` — capability gaps across task families

## Phased Rollout

### Phase A: Profile Schema and Fingerprinting
- Define `profile.yaml` schema
- Implement `plugin_features` extraction from manifests
- Implement `profile_fingerprint` aggregation
- Store fingerprints alongside existing v0.4 results
- No prediction yet, no community features yet

### Phase B: Plugin Utilization Audit
- Annotate transcripts with plugin ownership of each tool call
- Detect plugins that loaded but were never invoked
- Add Plugin Utilization section to per-run reports
- This is valuable even before the prediction layer exists

### Phase C: Layer 2 Prediction
- Build k-NN index over accumulated fingerprints
- Implement pre-run prediction with confidence bands
- Add "predicted vs actual" calibration tracking
- Threshold to enable: 30+ distinct profile fingerprints in historical data

### Phase D: fANOVA and Community Insights
- Fit Random Forest surrogate over fingerprint features
- Compute factor importance and interaction terms
- Generate plugin leaderboard, overhyped/underrated lists
- Publish first ecosystem report
- Threshold to enable: 100+ distinct profile fingerprints

### Phase E: ClawHub Integration
- Sync ClawHub package metadata into manifest cache
- Allow profile submissions to reference `clawhub:<package>@<version>`
- Push back ClawBench impact scores as a ClawHub package field
- Enable plugin authors to claim their packages and view detailed performance reports

## Success Criteria for v0.5

ClawBench v0.5 is successful if:

1. The same model scored under different plugin profiles produces score differences larger than the differences between frontier models on the same profile. This validates that configuration matters and that the benchmark measures it.
2. Pre-run predictions for new profiles, after 100+ submissions, achieve mean absolute calibration error below 0.08.
3. At least three plugin interaction effects are discovered empirically that no plugin manifest predicted.
4. At least one ClawHub plugin is identified as overhyped (high installs, low measured impact) and at least one as underrated (low installs, high impact).
5. Plugin authors begin submitting profiles specifically to validate or showcase their plugins, indicating the benchmark has become a useful tool for the ecosystem.
6. All v0.4 deterministic guarantees are preserved: scores remain reproducible, the verifier remains the source of truth, and no LLM judge enters the primary scoring path.

## What This Is Not

ClawBench v0.5 is not a model leaderboard. It is not a scaffold beauty contest. It is not a marketplace. It is a measurement instrument for the open plugin ecosystem that OpenClaw enables — the first benchmark that can answer the question "what does this configuration actually contribute, and what should I change?" with evidence rather than intuition.
