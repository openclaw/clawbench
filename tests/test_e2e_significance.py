"""End-to-end significance test for the ClawBench v0.5 framework.

This is the proof-of-meaningfulness test. It builds a realistic ecosystem
of plugin profiles and benchmark results, then asks: does the framework
actually surface real, actionable signal?

We define "significant enough to be a useful indicator" as:

  1. Score variance across profiles must be ≥ 0.15 (the framework must
     actually distinguish weak from strong configurations).
  2. The fANOVA decomposition must identify the seeded "true" effects
     (we know which features we built into the synthetic ecosystem and
     the framework should rediscover them).
  3. The k-NN predictor must achieve mean absolute error < 0.20 on a
     held-out profile (predictions must be calibrated, not random).
  4. The framework must surface at least one true interaction effect
     (we seed memory × browser as synergistic; the framework must find it).
  5. Surprise detection must distinguish a profile that is ABOVE its
     prediction from one that is BELOW.
  6. The framework must handle a never-before-seen plugin gracefully and
     still produce a useful prediction.

If this test passes, the framework is meaningful — not just functional.
"""

from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clawbench.diagnostic import build_diagnostic
from clawbench.factor_analysis import analyze
from clawbench.prediction import HistoricalDatabase, HistoricalRun, predict_profile
from clawbench.profile import (
    PluginManifest,
    PluginProfile,
    PluginProfileEntry,
    ProfileFingerprint,
    RegistrationTrace,
)


# ---------------------------------------------------------------------------
# Realistic synthetic ecosystem with KNOWN ground-truth effects.
#
# The "true" effect model (what the framework should discover):
#   - having memory                  → +0.10 base
#   - having browser                 → +0.08 base
#   - memory × browser SYNERGY       → +0.06 extra (the interaction we plant)
#   - having delegation              → +0.05 base
#   - having planning hooks          → +0.04 base
#   - having a code-review hook      → +0.03 base on coding tasks only
#   - random model noise             → ±0.05
#
# We generate N=40 profiles by randomly sampling from these features and
# computing scores from the additive model + interaction + noise. Then we
# check if the framework rediscovers the components.
# ---------------------------------------------------------------------------


PLUGIN_LIBRARY = {
    # plugin_id: (manifest_dict, registration_trace_dict)
    "anthropic": {
        "manifest": {"id": "anthropic"},
        "trace": {},
    },
    "memory-lancedb": {
        "manifest": {
            "id": "memory-lancedb",
            "kind": ["memory"],
            "contracts": {
                "memoryEmbeddingProviders": ["lancedb"],
                "tools": ["memory_write", "memory_read"],
            },
            "capabilityTags": ["memory", "vector-search"],
            "clawhub_is_official": True,
        },
        "trace": {
            "tools": ["memory_write", "memory_read"],
            "tool_families_seen": ["memory"],
        },
    },
    "memory-pinecone": {
        # Different implementation, same structural role
        "manifest": {
            "id": "memory-pinecone",
            "kind": ["memory"],
            "contracts": {
                "memoryEmbeddingProviders": ["pinecone"],
                "tools": ["pinecone_query", "pinecone_upsert"],
            },
            "capabilityTags": ["memory", "vector-search"],
        },
        "trace": {
            "tools": ["pinecone_query", "pinecone_upsert"],
            "tool_families_seen": ["memory", "search"],
        },
    },
    "browser-playwright": {
        "manifest": {
            "id": "browser-playwright",
            "contracts": {"tools": ["browser_navigate", "browser_click", "browser_extract"]},
            "capabilityTags": ["browser", "scraping"],
            "clawhub_is_official": True,
        },
        "trace": {
            "tools": ["browser_navigate", "browser_click", "browser_extract"],
            "tool_families_seen": ["browser"],
        },
    },
    "delegation-orchestrator": {
        "manifest": {
            "id": "delegation-orchestrator",
            "contracts": {"tools": ["spawn_agent", "wait_agent"]},
            "capabilityTags": ["delegation"],
        },
        "trace": {
            "tools": ["spawn_agent", "wait_agent"],
            "tool_families_seen": ["delegate"],
            "hooks": ["subagent_spawning", "subagent_ended"],
        },
    },
    "planning-enforcer": {
        "manifest": {
            "id": "planning-enforcer",
            "capabilityTags": ["planning"],
        },
        "trace": {
            "hooks": ["before_agent_start", "before_prompt_build"],
        },
    },
    "code-reviewer": {
        "manifest": {
            "id": "code-reviewer",
            "contracts": {"tools": ["review_file", "suggest_fix"]},
            "capabilityTags": ["code-quality"],
        },
        "trace": {
            "tools": ["review_file", "suggest_fix"],
            "tool_families_seen": ["read", "edit"],
            "hooks": ["after_tool_call"],
        },
    },
    "image-gen-openai": {
        "manifest": {
            "id": "image-gen-openai",
            "contracts": {
                "imageGenerationProviders": ["openai-dalle"],
                "tools": ["image_generate"],
            },
            "capabilityTags": ["image-generation"],
        },
        "trace": {
            "tools": ["image_generate"],
            "tool_families_seen": ["edit"],
        },
    },
    "web-search-tavily": {
        "manifest": {
            "id": "web-search-tavily",
            "contracts": {
                "webSearchProviders": ["tavily"],
                "tools": ["web_search"],
            },
            "capabilityTags": ["web-search"],
        },
        "trace": {
            "tools": ["web_search"],
            "tool_families_seen": ["search"],
        },
    },
}


def make_manifest(plugin_id: str) -> PluginManifest:
    return PluginManifest.from_dict(PLUGIN_LIBRARY[plugin_id]["manifest"])


def make_trace(plugin_id: str) -> RegistrationTrace:
    raw = PLUGIN_LIBRARY[plugin_id].get("trace", {})
    return RegistrationTrace(
        plugin_id=plugin_id,
        tools=raw.get("tools", []),
        tool_families_seen=raw.get("tool_families_seen", []),
        hooks=raw.get("hooks", []),
    )


# ---------------------------------------------------------------------------
# Ground-truth scoring function (what the framework should rediscover)
# ---------------------------------------------------------------------------


def true_score(plugin_set: set[str], rng: random.Random) -> float:
    """Compute the 'true' score for a given plugin set.

    This is the ground truth the framework must rediscover from observed
    benchmark results alone.
    """
    score = 0.45  # base
    has_memory = bool(plugin_set & {"memory-lancedb", "memory-pinecone"})
    has_browser = "browser-playwright" in plugin_set
    has_delegation = "delegation-orchestrator" in plugin_set
    has_planning = "planning-enforcer" in plugin_set
    has_code_review = "code-reviewer" in plugin_set
    has_web_search = "web-search-tavily" in plugin_set
    has_image_gen = "image-gen-openai" in plugin_set

    if has_memory:
        score += 0.10
    if has_browser:
        score += 0.08
    if has_memory and has_browser:
        score += 0.06  # the seeded interaction
    if has_delegation:
        score += 0.05
    if has_planning:
        score += 0.04
    if has_code_review:
        score += 0.03
    if has_web_search:
        score += 0.04
    if has_image_gen:
        score += 0.01

    # Small random noise to mimic real benchmark variance
    score += rng.gauss(0, 0.03)
    return round(max(0.0, min(1.0, score)), 4)


def make_per_task_scores(plugin_set: set[str], overall: float, rng: random.Random) -> dict[str, float]:
    """Generate per-task scores that vary realistically around overall."""
    tasks = [
        "t1-fs-quick-note",
        "t2-msg-write-email",
        "t3-fs-incident-bundle",
        "t3-msg-inbox-triage",
        "t4-life-trip-plan",
        "t3-web-research-and-cite",
        "t4-skill-quarterly-bundle",
    ]
    has_browser = "browser-playwright" in plugin_set
    per_task: dict[str, float] = {}
    for task_id in tasks:
        delta = rng.gauss(0, 0.08)
        # Bias browser tasks higher when browser is present
        if "web" in task_id and has_browser:
            delta += 0.10
        if "skill" in task_id and "delegation-orchestrator" in plugin_set:
            delta += 0.08
        per_task[task_id] = round(max(0.0, min(1.0, overall + delta)), 4)
    return per_task


# ---------------------------------------------------------------------------
# Profile generators
# ---------------------------------------------------------------------------


ALL_PLUGIN_IDS = list(PLUGIN_LIBRARY.keys())
NON_BASE_PLUGINS = [p for p in ALL_PLUGIN_IDS if p != "anthropic"]


def random_profile(name: str, rng: random.Random) -> tuple[PluginProfile, set[str]]:
    """Sample a random profile by independently flipping each non-base plugin."""
    enabled = ["anthropic"]
    for plugin_id in NON_BASE_PLUGINS:
        if rng.random() < 0.5:
            enabled.append(plugin_id)

    slots: dict[str, str] = {}
    for cand in ("memory-lancedb", "memory-pinecone"):
        if cand in enabled:
            slots["memory"] = cand
            break

    profile = PluginProfile(
        name=name,
        base_model="claude-sonnet-4",
        plugins=[PluginProfileEntry(id=p) for p in enabled],
        slots=slots,
        tools_allow=[],
    )
    return profile, set(enabled)


def build_ecosystem(n_profiles: int = 40, seed: int = 17):
    rng = random.Random(seed)
    manifests = {pid: make_manifest(pid) for pid in ALL_PLUGIN_IDS}
    traces = {pid: make_trace(pid) for pid in ALL_PLUGIN_IDS}

    db = HistoricalDatabase()
    profile_records = []
    for i in range(n_profiles):
        profile, plugin_set = random_profile(f"profile-{i:03d}", rng)
        overall = true_score(plugin_set, rng)
        per_task = make_per_task_scores(plugin_set, overall, rng)
        fingerprint = ProfileFingerprint.from_profile(profile, manifests, traces)
        db.add(HistoricalRun(
            profile_name=profile.name,
            fingerprint=fingerprint,
            overall_score=overall,
            per_task_score=per_task,
        ))
        profile_records.append((profile, plugin_set, overall, per_task))

    return db, manifests, traces, profile_records


# ---------------------------------------------------------------------------
# The significance tests
# ---------------------------------------------------------------------------


def test_score_variance_meaningful():
    db, _, _, _ = build_ecosystem(n_profiles=40)
    scores = [r.overall_score for r in db.runs]
    spread = max(scores) - min(scores)
    stdev = statistics.stdev(scores)
    print(f"  spread = {spread:.3f}, stdev = {stdev:.3f}")
    print(f"  min = {min(scores):.3f}, max = {max(scores):.3f}")
    assert spread >= 0.15, (
        f"score spread {spread:.3f} too small — framework cannot distinguish profiles"
    )
    assert stdev >= 0.05, (
        f"score stdev {stdev:.3f} too small — framework lacks signal"
    )
    print(f"  ✓ score spread {spread:.3f} and stdev {stdev:.3f} are sufficient")


def test_fanova_recovers_seeded_effects():
    db, _, _, _ = build_ecosystem(n_profiles=40)
    report = analyze(db, top_k_interactions=10)
    print(f"  factor analysis on {report.n_runs} runs, total variance = {report.total_variance:.4f}")

    # The framework should rediscover that memory and browser are the
    # strongest main effects we seeded.
    seeded_strong = [
        "tool_family:memory",
        "tool_family:browser",
        "capability:memory_embedding_providers",
    ]
    found_strong = []
    print("  top 10 main effects:")
    for me in report.main_effects[:10]:
        marker = "★" if me.feature in seeded_strong else " "
        print(f"    {marker} {me.feature:42}  importance={me.importance:.3f}  Δ={me.delta:+.3f}")
        if me.feature in seeded_strong and me.importance >= 0.05:
            found_strong.append(me.feature)

    assert len(found_strong) >= 2, (
        f"fANOVA failed to rediscover seeded main effects "
        f"(found only {found_strong})"
    )
    print(f"  ✓ rediscovered {len(found_strong)} of {len(seeded_strong)} seeded main effects")


def test_fanova_finds_seeded_interaction():
    db, _, _, _ = build_ecosystem(n_profiles=40)
    report = analyze(db, top_k_interactions=15)
    print("  top 15 interactions:")
    found_memory_browser = False
    for inter in report.interactions:
        is_seeded = (
            ("memory" in inter.feature_a and "browser" in inter.feature_b)
            or ("browser" in inter.feature_a and "memory" in inter.feature_b)
        )
        marker = "★" if is_seeded else " "
        print(f"    {marker} {inter.feature_a} × {inter.feature_b}  → residual {inter.interaction_strength:+.3f}")
        if is_seeded and inter.interaction_strength >= 0.02:
            found_memory_browser = True

    assert found_memory_browser, (
        "fANOVA failed to find the seeded memory × browser interaction"
    )
    print("  ✓ rediscovered the seeded memory × browser synergy")


def test_prediction_calibration():
    """Held-out test: predict the score of profiles the predictor has not seen."""
    full_db, manifests, traces, records = build_ecosystem(n_profiles=40)

    # Use the first 30 as training, last 10 as held-out test
    train_db = HistoricalDatabase()
    for r in full_db.runs[:30]:
        train_db.add(r)
    test_records = records[30:]

    errors = []
    for profile, plugin_set, actual, _ in test_records:
        fp = ProfileFingerprint.from_profile(profile, manifests, traces)
        pred = predict_profile(fp, train_db)
        err = abs(pred.predicted_overall_score - actual)
        errors.append(err)

    mae = statistics.mean(errors)
    median_err = statistics.median(errors)
    print(f"  predictions on {len(test_records)} held-out profiles:")
    print(f"    mean absolute error    = {mae:.4f}")
    print(f"    median absolute error  = {median_err:.4f}")
    print(f"    max absolute error     = {max(errors):.4f}")

    assert mae < 0.10, (
        f"mean absolute prediction error {mae:.4f} too large — "
        "framework predictions are not calibrated enough to be useful"
    )
    print(f"  ✓ MAE {mae:.4f} below useful-indicator threshold (0.10)")


def test_surprise_detection_distinguishes_outperformers():
    """Build two new profiles, manually perturb their scores up/down, and
    check the framework correctly classifies them as positive/negative surprises.
    """
    db, manifests, traces, records = build_ecosystem(n_profiles=40)
    rng = random.Random(99)

    # Build a baseline profile and pretend it scored MUCH higher than expected
    profile_up, plugin_set_up = random_profile("profile-up", rng)
    expected = true_score(plugin_set_up, random.Random(0))
    boosted_per_task = make_per_task_scores(plugin_set_up, expected + 0.30, rng)
    boosted_overall = round(min(1.0, expected + 0.30), 4)

    report_up = build_diagnostic(
        profile=profile_up,
        manifests=manifests,
        db=db,
        actual_overall_score=boosted_overall,
        actual_per_task_scores=boosted_per_task,
        traces=traces,
    )
    positives = [s for s in report_up.surprises if s.direction == "positive"]
    print(f"  upward-perturbed profile produced {len(positives)} positive surprises")
    assert positives, "framework failed to detect upward outperformance"

    # Build another profile and pretend it scored MUCH lower than expected
    profile_down, plugin_set_down = random_profile("profile-down", rng)
    expected_down = true_score(plugin_set_down, random.Random(0))
    suppressed_per_task = make_per_task_scores(plugin_set_down, max(0.0, expected_down - 0.30), rng)
    suppressed_overall = round(max(0.0, expected_down - 0.30), 4)

    report_down = build_diagnostic(
        profile=profile_down,
        manifests=manifests,
        db=db,
        actual_overall_score=suppressed_overall,
        actual_per_task_scores=suppressed_per_task,
        traces=traces,
    )
    negatives = [s for s in report_down.surprises if s.direction == "negative"]
    print(f"  downward-perturbed profile produced {len(negatives)} negative surprises")
    assert negatives, "framework failed to detect downward underperformance"
    print("  ✓ surprise detection correctly distinguishes outperformers from underperformers")


def test_unknown_plugin_graceful_prediction():
    """A profile referencing a never-before-seen plugin must still get a prediction."""
    db, manifests, _, _ = build_ecosystem(n_profiles=40)

    # Build a profile with a plugin that does not exist anywhere in the
    # historical data or the manifest library.
    profile = PluginProfile(
        name="novel-stack",
        base_model="claude-sonnet-4",
        plugins=[
            PluginProfileEntry(id="anthropic"),
            PluginProfileEntry(id="memory-lancedb"),
            PluginProfileEntry(id="brand-new-rag-engine", source="clawhub"),  # unknown
        ],
        slots={"memory": "memory-lancedb"},
        tools_allow=[],
    )
    # We deliberately pass manifests WITHOUT the unknown plugin
    fp = ProfileFingerprint.from_profile(profile, manifests, traces=None)
    assert fp.n_plugins == 3
    pred = predict_profile(fp, db)
    print(f"  predicted overall score = {pred.predicted_overall_score:.3f}")
    print(f"  confidence = {pred.confidence:.3f}")
    print(f"  used {pred.n_neighbors_used} neighbors")
    assert pred.predicted_overall_score > 0.4, (
        "prediction for an unknown-plugin profile should still be in a sane range"
    )
    print("  ✓ unknown plugin handled gracefully with non-zero prediction")


def test_full_diagnostic_renders_meaningful_report():
    db, manifests, traces, records = build_ecosystem(n_profiles=40)

    profile, plugin_set, actual, per_task = records[5]
    report = build_diagnostic(
        profile=profile,
        manifests=manifests,
        db=db,
        actual_overall_score=actual,
        actual_per_task_scores=per_task,
        traces=traces,
    )
    text = report.render_text()
    assert len(text) > 500
    assert "ClawBench Configuration Diagnostic" in text
    assert "Factor Analysis" in text
    print(text)
    print("  ✓ full diagnostic report renders with all sections")


def test_significance_summary():
    """Top-level: print a summary of how meaningful the framework is."""
    db, manifests, traces, records = build_ecosystem(n_profiles=40)
    report = analyze(db)
    scores = [r.overall_score for r in db.runs]
    print()
    print("  ════════════════════════════════════════════════")
    print("  FRAMEWORK SIGNIFICANCE SUMMARY")
    print("  ════════════════════════════════════════════════")
    print(f"  ecosystem size:           {len(db)} profiles")
    print(f"  score range:              [{min(scores):.3f}, {max(scores):.3f}]")
    print(f"  score stdev:              {statistics.stdev(scores):.4f}")
    print(f"  total variance:           {report.total_variance:.4f}")
    print(f"  features with importance>0.05: {sum(1 for m in report.main_effects if m.importance > 0.05)}")
    print(f"  interactions with strength>0.02: {sum(1 for i in report.interactions if i.interaction_strength > 0.02)}")
    print()
    print("  TOP 5 MAIN EFFECTS:")
    for m in report.main_effects[:5]:
        print(f"    {m.feature:42}  importance={m.importance:.3f}  Δ={m.delta:+.3f}")
    print()
    print("  TOP 3 INTERACTIONS:")
    for i in report.interactions[:3]:
        print(f"    {i.feature_a} × {i.feature_b}  → residual {i.interaction_strength:+.3f}")
    print("  ════════════════════════════════════════════════")
    print()


def main():
    tests = [
        test_score_variance_meaningful,
        test_fanova_recovers_seeded_effects,
        test_fanova_finds_seeded_interaction,
        test_prediction_calibration,
        test_surprise_detection_distinguishes_outperformers,
        test_unknown_plugin_graceful_prediction,
        test_full_diagnostic_renders_meaningful_report,
        test_significance_summary,
    ]
    failed = 0
    for fn in tests:
        name = fn.__name__
        print(f"\n=== {name} ===")
        try:
            fn()
        except AssertionError as e:
            print(f"  ✗ FAIL: {e}")
            failed += 1
        except Exception as e:
            import traceback
            print(f"  ✗ ERROR: {e}")
            traceback.print_exc()
            failed += 1
    print()
    print("=" * 70)
    if failed:
        print(f"  {failed} of {len(tests)} significance tests FAILED")
        sys.exit(1)
    print(f"  all {len(tests)} significance tests passed — framework is meaningful")


if __name__ == "__main__":
    main()
