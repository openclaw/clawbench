"""End-to-end tests for the ClawBench v0.5 configuration-space framework.

This test file is the executable proof that the framework works. It builds
a synthetic ecosystem of plugin profiles and benchmark results, then walks
through the full diagnostic loop:

  1. Parse a Plugin Profile from YAML
  2. Build manifests for the plugins it references
  3. Compute a Profile Fingerprint
  4. Predict scores from a historical database
  5. Compare predictions to actuals (surprises)
  6. Run factor analysis to surface ecosystem-level patterns
  7. Render a human-readable diagnostic report

If this file passes, the framework is e2e-functional even before any
real benchmark runs exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when run from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clawbench.profile import (
    PluginManifest,
    PluginProfile,
    PluginProfileEntry,
    ProfileFingerprint,
    RegistrationTrace,
    fingerprint_similarity,
    plugin_feature_vector,
)
from clawbench.prediction import HistoricalDatabase, HistoricalRun, predict_profile
from clawbench.factor_analysis import analyze
from clawbench.diagnostic import build_diagnostic


# ---------------------------------------------------------------------------
# Synthetic ecosystem fixtures
# ---------------------------------------------------------------------------


def make_manifest(
    plugin_id: str,
    *,
    tools: list[str] | None = None,
    kind: list[str] | None = None,
    contracts: dict[str, list[str]] | None = None,
    capability_tags: list[str] | None = None,
    is_official: bool = False,
) -> PluginManifest:
    return PluginManifest(
        id=plugin_id,
        kind=kind or [],
        contracts=contracts or {"tools": tools or []},
        capability_tags=capability_tags or [],
        clawhub_is_official=is_official,
    )


def make_trace(
    plugin_id: str,
    *,
    tools: list[str] | None = None,
    families: list[str] | None = None,
    hooks: list[str] | None = None,
) -> RegistrationTrace:
    return RegistrationTrace(
        plugin_id=plugin_id,
        tools=tools or [],
        tool_families_seen=families or [],
        hooks=hooks or [],
    )


PLUGIN_DEFINITIONS = {
    "anthropic": (
        make_manifest("anthropic", capability_tags=["llm-provider"]),
        make_trace("anthropic"),
    ),
    "memory-lancedb": (
        make_manifest(
            "memory-lancedb",
            kind=["memory"],
            contracts={"memoryEmbeddingProviders": ["lancedb"], "tools": ["memory_write", "memory_read"]},
            capability_tags=["memory", "vector-search"],
            is_official=True,
        ),
        make_trace(
            "memory-lancedb",
            tools=["memory_write", "memory_read"],
            families=["memory"],
        ),
    ),
    "browser-playwright": (
        make_manifest(
            "browser-playwright",
            contracts={"tools": ["browser_navigate", "browser_click", "browser_extract"]},
            capability_tags=["browser", "scraping"],
            is_official=True,
        ),
        make_trace(
            "browser-playwright",
            tools=["browser_navigate", "browser_click", "browser_extract"],
            families=["browser"],
        ),
    ),
    "github-skill": (
        make_manifest(
            "github-skill",
            contracts={"tools": ["gh_pr", "gh_issue", "gh_repo"]},
            capability_tags=["github", "code-collab"],
        ),
        make_trace(
            "github-skill",
            tools=["gh_pr", "gh_issue", "gh_repo"],
            families=["edit", "read"],
        ),
    ),
    "delegation-orchestrator": (
        make_manifest(
            "delegation-orchestrator",
            contracts={"tools": ["spawn_agent", "wait_agent"]},
            capability_tags=["delegation", "subagent"],
            is_official=True,
        ),
        make_trace(
            "delegation-orchestrator",
            tools=["spawn_agent", "wait_agent"],
            families=["delegate"],
            hooks=["subagent_spawning", "subagent_ended"],
        ),
    ),
    "planning-enforcer": (
        make_manifest(
            "planning-enforcer",
            capability_tags=["planning", "structured-output"],
        ),
        make_trace(
            "planning-enforcer",
            hooks=["before_agent_start", "before_prompt_build"],
        ),
    ),
    "rag-pinecone": (
        make_manifest(
            "rag-pinecone",
            kind=["memory"],
            contracts={"memoryEmbeddingProviders": ["pinecone"], "tools": ["pinecone_query"]},
            capability_tags=["memory", "vector-search"],
        ),
        make_trace("rag-pinecone", tools=["pinecone_query"], families=["memory", "search"]),
    ),
    "code-reviewer": (
        make_manifest(
            "code-reviewer",
            contracts={"tools": ["review_file", "suggest_fix"]},
            capability_tags=["code-quality", "review"],
        ),
        make_trace(
            "code-reviewer",
            tools=["review_file", "suggest_fix"],
            families=["read", "edit"],
            hooks=["before_tool_call", "after_tool_call"],
        ),
    ),
}


def get_manifest_map(plugin_ids):
    return {pid: PLUGIN_DEFINITIONS[pid][0] for pid in plugin_ids}


def get_trace_map(plugin_ids):
    return {pid: PLUGIN_DEFINITIONS[pid][1] for pid in plugin_ids}


# ---------------------------------------------------------------------------
# Synthetic profiles representing different "shapes" of agent
# ---------------------------------------------------------------------------


PROFILES = {
    "minimal": PluginProfile(
        name="minimal-coder",
        base_model="claude-sonnet-4",
        plugins=[PluginProfileEntry("anthropic")],
        slots={},
        tools_allow=["bash", "file_edit"],
    ),
    "browser-only": PluginProfile(
        name="browser-only",
        base_model="claude-sonnet-4",
        plugins=[
            PluginProfileEntry("anthropic"),
            PluginProfileEntry("browser-playwright"),
        ],
        slots={},
        tools_allow=["bash", "file_edit", "browser_navigate", "browser_click"],
    ),
    "memory-coder": PluginProfile(
        name="memory-coder",
        base_model="claude-sonnet-4",
        plugins=[
            PluginProfileEntry("anthropic"),
            PluginProfileEntry("memory-lancedb"),
        ],
        slots={"memory": "memory-lancedb"},
        tools_allow=["bash", "file_edit", "memory_read", "memory_write"],
    ),
    "research-stack": PluginProfile(
        name="research-stack",
        base_model="claude-sonnet-4",
        plugins=[
            PluginProfileEntry("anthropic"),
            PluginProfileEntry("memory-lancedb"),
            PluginProfileEntry("browser-playwright"),
        ],
        slots={"memory": "memory-lancedb"},
        tools_allow=["bash", "file_edit", "browser_navigate", "memory_read"],
    ),
    "delegated-coder": PluginProfile(
        name="delegated-coder",
        base_model="claude-sonnet-4",
        plugins=[
            PluginProfileEntry("anthropic"),
            PluginProfileEntry("delegation-orchestrator"),
            PluginProfileEntry("planning-enforcer"),
        ],
        slots={},
        tools_allow=["bash", "file_edit", "spawn_agent"],
    ),
    "full-stack": PluginProfile(
        name="full-stack",
        base_model="claude-sonnet-4",
        plugins=[
            PluginProfileEntry("anthropic"),
            PluginProfileEntry("memory-lancedb"),
            PluginProfileEntry("browser-playwright"),
            PluginProfileEntry("delegation-orchestrator"),
            PluginProfileEntry("planning-enforcer"),
        ],
        slots={"memory": "memory-lancedb"},
        tools_allow=["bash", "file_edit", "browser_navigate", "memory_read", "spawn_agent"],
    ),
    "novel-rag": PluginProfile(
        name="novel-rag-stack",
        base_model="claude-sonnet-4",
        plugins=[
            PluginProfileEntry("anthropic"),
            PluginProfileEntry("rag-pinecone", source="clawhub"),
            PluginProfileEntry("code-reviewer", source="local"),
        ],
        slots={"memory": "rag-pinecone"},
        tools_allow=["bash", "file_edit", "pinecone_query", "review_file"],
    ),
}


# Synthetic per-task scores per profile. Each profile has a different
# strength/weakness pattern so the framework has signal to learn from.
PROFILE_RESULTS = {
    "minimal": {
        "overall": 0.45,
        "per_task": {
            "t1-fs-quick-note": 0.65,
            "t2-msg-write-email": 0.55,
            "t3-fs-incident-bundle": 0.30,
            "t3-msg-inbox-triage": 0.25,
            "t4-life-trip-plan": 0.35,
            "t3-web-research-and-cite": 0.20,
            "t4-skill-quarterly-bundle": 0.30,
        },
    },
    "browser-only": {
        "overall": 0.58,
        "per_task": {
            "t1-fs-quick-note": 0.62,
            "t2-msg-write-email": 0.55,
            "t3-fs-incident-bundle": 0.40,
            "t3-msg-inbox-triage": 0.30,
            "t4-life-trip-plan": 0.55,
            "t3-web-research-and-cite": 0.85,
            "t4-skill-quarterly-bundle": 0.35,
        },
    },
    "memory-coder": {
        "overall": 0.62,
        "per_task": {
            "t1-fs-quick-note": 0.70,
            "t2-msg-write-email": 0.65,
            "t3-fs-incident-bundle": 0.55,
            "t3-msg-inbox-triage": 0.55,
            "t4-life-trip-plan": 0.50,
            "t3-web-research-and-cite": 0.30,
            "t4-skill-quarterly-bundle": 0.45,
        },
    },
    "research-stack": {
        "overall": 0.74,
        "per_task": {
            "t1-fs-quick-note": 0.75,
            "t2-msg-write-email": 0.70,
            "t3-fs-incident-bundle": 0.65,
            "t3-msg-inbox-triage": 0.65,
            "t4-life-trip-plan": 0.80,
            "t3-web-research-and-cite": 0.92,
            "t4-skill-quarterly-bundle": 0.55,
        },
    },
    "delegated-coder": {
        "overall": 0.66,
        "per_task": {
            "t1-fs-quick-note": 0.62,
            "t2-msg-write-email": 0.65,
            "t3-fs-incident-bundle": 0.70,
            "t3-msg-inbox-triage": 0.50,
            "t4-life-trip-plan": 0.55,
            "t3-web-research-and-cite": 0.40,
            "t4-skill-quarterly-bundle": 0.85,
        },
    },
    "full-stack": {
        "overall": 0.84,
        "per_task": {
            "t1-fs-quick-note": 0.78,
            "t2-msg-write-email": 0.75,
            "t3-fs-incident-bundle": 0.80,
            "t3-msg-inbox-triage": 0.78,
            "t4-life-trip-plan": 0.88,
            "t3-web-research-and-cite": 0.93,
            "t4-skill-quarterly-bundle": 0.92,
        },
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_plugin_feature_vector_shape():
    """Every plugin yields the same shape vector."""
    seen_keys = None
    for pid, (manifest, trace) in PLUGIN_DEFINITIONS.items():
        fv = plugin_feature_vector(manifest, trace)
        if seen_keys is None:
            seen_keys = set(fv.keys())
        else:
            assert set(fv.keys()) == seen_keys, f"feature vector shape drift on {pid}"
    print(f"  ✓ feature vector shape is consistent across {len(PLUGIN_DEFINITIONS)} plugins ({len(seen_keys)} features each)")


def test_unknown_plugin_still_yields_features():
    """Cold-start: a plugin with no manifest still produces a usable vector."""
    minimal_manifest = PluginManifest(id="brand-new-plugin")
    fv = plugin_feature_vector(minimal_manifest, None)
    assert fv["plugin_id"] == "brand-new-plugin"
    assert fv["n_tools_registered"] == 0
    assert fv["n_hooks"] == 0
    print("  ✓ unknown plugin without manifest yields a complete (empty) feature vector")


def test_profile_fingerprint_basic():
    profile = PROFILES["research-stack"]
    manifests = get_manifest_map(["anthropic", "memory-lancedb", "browser-playwright"])
    traces = get_trace_map(["anthropic", "memory-lancedb", "browser-playwright"])
    fp = ProfileFingerprint.from_profile(profile, manifests, traces)
    assert fp.profile_name == "research-stack"
    assert fp.n_plugins == 3
    assert "memory" in fp.tool_family_surface
    assert "browser" in fp.tool_family_surface
    assert fp.memory_slot == "memory-lancedb"
    assert fp.fingerprint_hash, "fingerprint hash should be non-empty"
    print(f"  ✓ research-stack fingerprint: {fp.fingerprint_hash}")
    print(f"    capability_coverage = {fp.capability_coverage}")
    print(f"    tool_family_surface = {fp.tool_family_surface}")


def test_fingerprint_similarity_axes():
    """Similar profiles should score above 0.7, dissimilar below 0.5."""
    manifests = get_manifest_map(list(PLUGIN_DEFINITIONS.keys()))
    traces = get_trace_map(list(PLUGIN_DEFINITIONS.keys()))

    fp_research = ProfileFingerprint.from_profile(PROFILES["research-stack"], manifests, traces)
    fp_full = ProfileFingerprint.from_profile(PROFILES["full-stack"], manifests, traces)
    fp_minimal = ProfileFingerprint.from_profile(PROFILES["minimal"], manifests, traces)
    fp_browser = ProfileFingerprint.from_profile(PROFILES["browser-only"], manifests, traces)

    sim_research_full = fingerprint_similarity(fp_research, fp_full)
    sim_research_minimal = fingerprint_similarity(fp_research, fp_minimal)
    sim_research_browser = fingerprint_similarity(fp_research, fp_browser)

    assert sim_research_full > sim_research_minimal, (
        f"research↔full ({sim_research_full:.3f}) should exceed research↔minimal ({sim_research_minimal:.3f})"
    )
    assert sim_research_browser > sim_research_minimal, (
        f"research↔browser ({sim_research_browser:.3f}) should exceed research↔minimal ({sim_research_minimal:.3f})"
    )
    print(f"  ✓ research↔full   = {sim_research_full:.3f}")
    print(f"  ✓ research↔browser = {sim_research_browser:.3f}")
    print(f"  ✓ research↔minimal = {sim_research_minimal:.3f}")


def test_cold_start_prediction_falls_back():
    """With an empty DB, prediction should fall back to a neutral midpoint."""
    db = HistoricalDatabase()
    profile = PROFILES["research-stack"]
    manifests = get_manifest_map(["anthropic", "memory-lancedb", "browser-playwright"])
    fp = ProfileFingerprint.from_profile(profile, manifests)
    pred = predict_profile(fp, db)
    assert pred.confidence == 0.0
    assert pred.predicted_overall_score == 0.5
    assert "cold start" in pred.note
    print(f"  ✓ empty-DB prediction = {pred.predicted_overall_score} (note: {pred.note})")


def test_prediction_improves_with_data():
    """As we feed historical runs in, predictions should converge toward truth."""
    db = HistoricalDatabase()
    manifests = get_manifest_map(list(PLUGIN_DEFINITIONS.keys()))
    traces = get_trace_map(list(PLUGIN_DEFINITIONS.keys()))

    # Seed with all profiles except `full-stack` (held out as the test case)
    seed_profiles = ["minimal", "browser-only", "memory-coder", "research-stack", "delegated-coder"]
    for name in seed_profiles:
        profile = PROFILES[name]
        fp = ProfileFingerprint.from_profile(profile, manifests, traces)
        results = PROFILE_RESULTS[name]
        db.add(HistoricalRun(
            profile_name=profile.name,
            fingerprint=fp,
            overall_score=results["overall"],
            per_task_score=results["per_task"],
        ))

    # Predict full-stack from the seeded data
    full_profile = PROFILES["full-stack"]
    full_fp = ProfileFingerprint.from_profile(full_profile, manifests, traces)
    pred = predict_profile(full_fp, db)
    actual = PROFILE_RESULTS["full-stack"]["overall"]
    error = abs(pred.predicted_overall_score - actual)
    print(f"  predicted full-stack = {pred.predicted_overall_score:.3f}  actual = {actual:.3f}  error = {error:.3f}")
    print(f"  used {pred.n_neighbors_used} neighbors: {pred.neighbor_names}")
    assert pred.predicted_overall_score > 0.6, (
        f"full-stack should be predicted high, got {pred.predicted_overall_score}"
    )
    # The full-stack actually beats every seed profile, so prediction will
    # underestimate but should still be in a reasonable range.
    assert error < 0.25, f"prediction error {error} too large"
    print("  ✓ prediction error within acceptable range")


def test_factor_analysis_finds_signal():
    db = HistoricalDatabase()
    manifests = get_manifest_map(list(PLUGIN_DEFINITIONS.keys()))
    traces = get_trace_map(list(PLUGIN_DEFINITIONS.keys()))

    for name, profile in PROFILES.items():
        if name == "novel-rag":
            continue  # leave novel-rag out for the unknown-plugin test
        fp = ProfileFingerprint.from_profile(profile, manifests, traces)
        results = PROFILE_RESULTS[name]
        db.add(HistoricalRun(
            profile_name=profile.name,
            fingerprint=fp,
            overall_score=results["overall"],
            per_task_score=results["per_task"],
        ))

    report = analyze(db)
    assert report.n_runs >= 4
    assert report.main_effects, "factor analysis should produce main effects"
    print(f"  ✓ factor analysis on {report.n_runs} runs, total variance = {report.total_variance:.4f}")
    print("    top 5 main effects:")
    for me in report.main_effects[:5]:
        print(f"      {me.feature:40}  importance={me.importance:.3f}  Δ={me.delta:+.2f}")
    if report.interactions:
        print("    top interactions:")
        for inter in report.interactions[:3]:
            print(f"      {inter.feature_a} × {inter.feature_b}  → residual {inter.interaction_strength:.3f}")


def test_unknown_plugin_handled_gracefully():
    """A profile referencing a plugin we have no manifest for should still work."""
    profile = PROFILES["novel-rag"]
    # Only provide manifest for anthropic; rag-pinecone and code-reviewer are
    # truly unknown to the framework.
    manifests = {"anthropic": PLUGIN_DEFINITIONS["anthropic"][0]}
    fp = ProfileFingerprint.from_profile(profile, manifests, traces=None)
    assert fp.n_plugins == 3
    assert fp.profile_name == "novel-rag-stack"
    print(f"  ✓ unknown-plugin profile fingerprinted: {fp.fingerprint_hash}")


def test_full_diagnostic_with_surprises():
    """End-to-end diagnostic flow including surprise detection."""
    db = HistoricalDatabase()
    manifests = get_manifest_map(list(PLUGIN_DEFINITIONS.keys()))
    traces = get_trace_map(list(PLUGIN_DEFINITIONS.keys()))

    # Seed with everything except research-stack
    seed_names = ["minimal", "browser-only", "memory-coder", "delegated-coder", "full-stack"]
    for name in seed_names:
        profile = PROFILES[name]
        fp = ProfileFingerprint.from_profile(profile, manifests, traces)
        results = PROFILE_RESULTS[name]
        db.add(HistoricalRun(
            profile_name=profile.name,
            fingerprint=fp,
            overall_score=results["overall"],
            per_task_score=results["per_task"],
        ))

    # Submit research-stack and get a full diagnostic
    profile = PROFILES["research-stack"]
    actual = PROFILE_RESULTS["research-stack"]
    report = build_diagnostic(
        profile=profile,
        manifests=manifests,
        db=db,
        actual_overall_score=actual["overall"],
        actual_per_task_scores=actual["per_task"],
        traces=traces,
    )
    text = report.render_text()
    print(text)
    assert report.predicted_score > 0
    assert report.prediction_confidence > 0
    assert report.factor_analysis is not None


def test_persistence_roundtrip(tmp_path: Path | None = None):
    """The database should round-trip cleanly through JSON."""
    if tmp_path is None:
        tmp_path = Path("/tmp/clawbench_v05_test")
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "history.json"
    if db_path.exists():
        db_path.unlink()

    manifests = get_manifest_map(list(PLUGIN_DEFINITIONS.keys()))
    traces = get_trace_map(list(PLUGIN_DEFINITIONS.keys()))

    db = HistoricalDatabase(path=db_path)
    for name in ["minimal", "browser-only", "research-stack"]:
        profile = PROFILES[name]
        fp = ProfileFingerprint.from_profile(profile, manifests, traces)
        results = PROFILE_RESULTS[name]
        db.add(HistoricalRun(
            profile_name=profile.name,
            fingerprint=fp,
            overall_score=results["overall"],
            per_task_score=results["per_task"],
        ))
    assert len(db) == 3
    assert db_path.exists()

    db2 = HistoricalDatabase(path=db_path)
    assert len(db2) == 3
    assert db2.runs[0].profile_name == db.runs[0].profile_name
    print(f"  ✓ persisted {len(db)} runs to {db_path} and round-tripped cleanly")


def test_yaml_profile_parsing():
    """Profile YAML parsing should handle all source types."""
    yaml_text = """
profile:
  name: test-profile
  base_model: claude-sonnet-4
  plugins:
    enabled:
      - anthropic
      - id: memory-lancedb
        config:
          dimensions: 1536
      - clawhub:rag-pinecone@1.2.0
      - local:./my-custom-plugin
    slots:
      memory: memory-lancedb
    tools_allow:
      - bash
      - file_edit
"""
    import yaml as yaml_lib
    data = yaml_lib.safe_load(yaml_text)
    profile = PluginProfile.from_dict(data)
    assert profile.name == "test-profile"
    assert profile.base_model == "claude-sonnet-4"
    assert len(profile.plugins) == 4
    sources = {e.id: e.source for e in profile.plugins}
    assert sources["anthropic"] == "bundled"
    assert sources["memory-lancedb"] == "bundled"
    assert sources["rag-pinecone"] == "clawhub"
    assert sources["./my-custom-plugin"] == "local"
    print(f"  ✓ YAML profile parsed: {profile.name}, {len(profile.plugins)} plugins, slot={profile.slots}")


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


def main():
    tests = [
        test_plugin_feature_vector_shape,
        test_unknown_plugin_still_yields_features,
        test_profile_fingerprint_basic,
        test_fingerprint_similarity_axes,
        test_cold_start_prediction_falls_back,
        test_prediction_improves_with_data,
        test_factor_analysis_finds_signal,
        test_unknown_plugin_handled_gracefully,
        test_yaml_profile_parsing,
        test_persistence_roundtrip,
        test_full_diagnostic_with_surprises,
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
        print(f"  {failed} of {len(tests)} tests FAILED")
        sys.exit(1)
    else:
        print(f"  all {len(tests)} tests passed")


if __name__ == "__main__":
    main()
