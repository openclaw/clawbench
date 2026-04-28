"""Unit tests for the v0.5 extensions shipped in this pass:

- Taguchi S/N robustness profile
- Plugin Utilization Audit
- Manifest-vs-Reality Gap
- Calibration tracking in HistoricalDatabase
- Recommendations generator
- Surprise cause attribution
- Insights publishing
- End-to-end diagnostic with all sections populated

These tests run in isolation from the larger harness; they build small
synthetic fixtures and exercise the pure-function paths only.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clawbench.diagnostic import build_diagnostic, submit_run
from clawbench.diagnose_cli import infer_registration_traces_from_manifests
from clawbench.factor_analysis import analyze
from clawbench.insights import (
    compute_capability_gaps,
    compute_plugin_leaderboard,
    publish_insights,
)
from clawbench.prediction import (
    HistoricalDatabase,
    HistoricalRun,
    attribute_surprise,
)
from clawbench.profile import (
    PluginManifest,
    PluginProfile,
    PluginProfileEntry,
    ProfileFingerprint,
    RegistrationTrace,
)
from clawbench.recommendations import generate_recommendations
from clawbench.schemas import ToolCall, Transcript, TranscriptMessage
from clawbench.stats import (
    compute_robustness_profile,
    taguchi_sn_larger_is_better,
)
from clawbench.utilization import (
    audit_plugin_utilization,
    compute_manifest_reality_gap,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(
    name: str, plugin_ids: list[str], *, base_model: str = "claude-sonnet-4"
) -> PluginProfile:
    entries = [PluginProfileEntry(id=pid, source="bundled") for pid in plugin_ids]
    return PluginProfile(
        name=name,
        base_model=base_model,
        plugins=entries,
        slots={"memory": "memory-lancedb"} if "memory-lancedb" in plugin_ids else {},
    )


def _make_manifest(
    pid: str,
    *,
    tools: list[str] | None = None,
    kind: list[str] | None = None,
) -> PluginManifest:
    return PluginManifest(
        id=pid,
        kind=kind or [],
        contracts={"tools": tools or []},
    )


def _make_transcript(tool_calls: list[tuple[str, str]]) -> Transcript:
    """Build a transcript from [(tool_name, family), ...]."""
    calls = [
        ToolCall(name=name, family=family, output="ok", success=True)
        for name, family in tool_calls
    ]
    return Transcript(
        messages=[TranscriptMessage(role="assistant", tool_calls=calls)]
    )


# ---------------------------------------------------------------------------
# Taguchi S/N + robustness profile
# ---------------------------------------------------------------------------


def test_taguchi_sn_penalizes_worst_case_harder_than_mean():
    """A configuration that averages high but crashes on one task should
    have a worse S/N ratio than one with a lower mean but no crashes."""
    balanced = [0.68, 0.70, 0.68, 0.72, 0.70]
    spikey = [0.95, 0.97, 0.96, 0.94, 0.05]

    mean_balanced = sum(balanced) / len(balanced)
    mean_spikey = sum(spikey) / len(spikey)
    assert mean_spikey > mean_balanced  # spikey wins on mean

    sn_balanced = taguchi_sn_larger_is_better(balanced)
    sn_spikey = taguchi_sn_larger_is_better(spikey)
    # S/N should flip the ranking — the 0.05 crash in spikey drags
    # -10*log10(mean_inv_square) down below the steady 0.70 baseline.
    assert sn_balanced > sn_spikey


def test_compute_robustness_profile_fills_tier_means():
    scores = {"t1-a": 0.9, "t1-b": 0.8, "t2-a": 0.5, "t3-a": 0.3}
    tiers = {"t1-a": "tier1", "t1-b": "tier1", "t2-a": "tier2", "t3-a": "tier3"}
    rp = compute_robustness_profile(scores, tier_of=tiers)
    assert rp.n_tasks == 4
    assert rp.worst_of_n == 0.3
    assert rp.best_of_n == 0.9
    assert abs(rp.tier_means["tier1"] - 0.85) < 1e-6
    assert abs(rp.tier_means["tier2"] - 0.5) < 1e-6


def test_taguchi_sn_handles_zero_score_without_crashing():
    # 0 would be 1/0 without the floor — should not raise
    sn = taguchi_sn_larger_is_better([0.0, 0.5, 1.0])
    assert math.isfinite(sn)


# ---------------------------------------------------------------------------
# Plugin Utilization Audit
# ---------------------------------------------------------------------------


def test_infer_registration_traces_from_manifests_uses_declared_tools():
    profile = _make_profile("p", ["alpha", "missing"])
    manifests = {
        "alpha": _make_manifest(
            "alpha",
            tools=["read_file", "browser_click", "memory_write"],
        ),
    }

    traces = infer_registration_traces_from_manifests(profile, manifests)

    assert set(traces) == {"alpha"}
    assert traces["alpha"].tools == ["read_file", "browser_click", "memory_write"]
    assert traces["alpha"].tool_families_seen == ["browser", "memory", "read"]


def test_audit_flags_dead_weight_plugin():
    profile = _make_profile("p", ["alpha", "beta"])
    manifests = {
        "alpha": _make_manifest("alpha", tools=["alpha_tool"]),
        "beta": _make_manifest("beta", tools=["beta_tool"]),
    }
    traces = {
        "alpha": RegistrationTrace(
            plugin_id="alpha",
            tools=["alpha_tool"],
            tool_families_seen=["read"],
        ),
        "beta": RegistrationTrace(
            plugin_id="beta",
            tools=["beta_tool"],
            tool_families_seen=["edit"],
        ),
    }
    transcripts = {
        "task-1": _make_transcript([("alpha_tool", "read")]),
        "task-2": _make_transcript([("alpha_tool", "read")]),
    }
    report = audit_plugin_utilization(
        profile=profile,
        transcripts=transcripts,
        manifests=manifests,
        traces=traces,
    )
    assert report.n_plugins == 2
    assert report.n_invoked == 1
    assert report.n_dead_weight == 1

    alpha = next(p for p in report.per_plugin if p.plugin_id == "alpha")
    beta = next(p for p in report.per_plugin if p.plugin_id == "beta")
    assert alpha.invocation_count == 2
    assert not alpha.dead_weight
    assert beta.invocation_count == 0
    assert beta.dead_weight


def test_audit_family_fallback_when_trace_missing_tool_name():
    profile = _make_profile("p", ["alpha"])
    manifests = {"alpha": _make_manifest("alpha")}
    traces = {
        "alpha": RegistrationTrace(
            plugin_id="alpha",
            tools=[],  # intentionally empty
            tool_families_seen=["search"],
        )
    }
    transcripts = {
        "t": _make_transcript([("unknown_tool", "search")]),
    }
    report = audit_plugin_utilization(
        profile=profile, transcripts=transcripts, manifests=manifests, traces=traces
    )
    alpha = next(p for p in report.per_plugin if p.plugin_id == "alpha")
    assert alpha.invoked
    assert alpha.invocation_count == 1


# ---------------------------------------------------------------------------
# Manifest-vs-Reality gap
# ---------------------------------------------------------------------------


def test_manifest_reality_gap_flags_unused_claims():
    profile = _make_profile("p", ["alpha"])
    manifest = PluginManifest(
        id="alpha",
        kind=["memory"],  # claims memory family
        contracts={"tools": ["alpha_tool"]},
    )
    manifests = {"alpha": manifest}
    traces = {
        "alpha": RegistrationTrace(
            plugin_id="alpha",
            tools=["alpha_tool"],
            tool_families_seen=["read"],  # observed, not claimed
        )
    }
    transcripts = {
        "t": _make_transcript([("alpha_tool", "read")]),
    }
    util = audit_plugin_utilization(
        profile=profile, transcripts=transcripts, manifests=manifests, traces=traces
    )
    gap = compute_manifest_reality_gap(
        profile=profile, manifests=manifests, utilization=util
    )
    assert len(gap.per_plugin) == 1
    g = gap.per_plugin[0]
    assert "memory" in g.claimed_capabilities
    assert "read" in g.observed_capabilities
    assert "memory" in g.unused_capabilities
    assert "read" in g.unclaimed_capabilities
    assert g.claim_coverage == 0.0


# ---------------------------------------------------------------------------
# Calibration tracking
# ---------------------------------------------------------------------------


def test_calibration_metrics_accumulate_across_runs(tmp_path):
    db = HistoricalDatabase(path=tmp_path / "runs.json")
    fp_kwargs = dict(
        base_model="m",
        capability_coverage=[],
        hook_footprint=[],
        tool_family_surface=[],
        capability_tags_union=[],
        memory_slot="",
        context_engine_slot="",
        n_plugins=0,
        n_clawhub_plugins=0,
        n_custom_plugins=0,
        n_official_plugins=0,
        n_tools_total=0,
        n_hooks_total=0,
        plugin_ids=[],
        tools_allow=[],
        fingerprint_hash="aaa",
    )
    db.add(HistoricalRun(
        profile_name="A",
        fingerprint=ProfileFingerprint(profile_name="A", **fp_kwargs),
        overall_score=0.80,
        predicted_score_at_submission=0.75,
    ))
    db.add(HistoricalRun(
        profile_name="B",
        fingerprint=ProfileFingerprint(profile_name="B", **fp_kwargs),
        overall_score=0.60,
        predicted_score_at_submission=0.65,
    ))
    m = db.calibration_metrics()
    assert m["n"] == 2
    # |0.80-0.75| + |0.60-0.65| = 0.10 → MAE = 0.05
    assert abs(m["mae"] - 0.05) < 1e-4
    # Not enough data for the target to be "met"
    assert m["mae_target_met"] is False


def test_calibration_metrics_handle_runs_without_prediction():
    db = HistoricalDatabase()
    fp_kwargs = dict(
        base_model="m",
        capability_coverage=[],
        hook_footprint=[],
        tool_family_surface=[],
        capability_tags_union=[],
        memory_slot="",
        context_engine_slot="",
        n_plugins=0,
        n_clawhub_plugins=0,
        n_custom_plugins=0,
        n_official_plugins=0,
        n_tools_total=0,
        n_hooks_total=0,
        plugin_ids=[],
        tools_allow=[],
        fingerprint_hash="aaa",
    )
    db.runs.append(HistoricalRun(
        profile_name="legacy",
        fingerprint=ProfileFingerprint(profile_name="legacy", **fp_kwargs),
        overall_score=0.5,
        predicted_score_at_submission=None,
    ))
    m = db.calibration_metrics()
    assert m["n"] == 0
    assert m["mae"] == 0.0


# ---------------------------------------------------------------------------
# Recommendations + surprise attribution + insights
# ---------------------------------------------------------------------------


def _fp(name: str, **kwargs) -> ProfileFingerprint:
    defaults = dict(
        profile_name=name,
        base_model="claude-sonnet-4",
        capability_coverage=[],
        hook_footprint=[],
        tool_family_surface=[],
        capability_tags_union=[],
        memory_slot="",
        context_engine_slot="",
        n_plugins=0,
        n_clawhub_plugins=0,
        n_custom_plugins=0,
        n_official_plugins=0,
        n_tools_total=0,
        n_hooks_total=0,
        plugin_ids=[],
        tools_allow=[],
        fingerprint_hash=name,
    )
    defaults.update(kwargs)
    return ProfileFingerprint(**defaults)


def _seed_database() -> HistoricalDatabase:
    """Build a database where 'magic' plugin clearly lifts scores."""
    db = HistoricalDatabase()
    for i, (pids, score) in enumerate([
        (["alpha", "magic"], 0.90),
        (["alpha", "magic"], 0.85),
        (["alpha", "magic", "beta"], 0.88),
        (["alpha"], 0.60),
        (["alpha"], 0.55),
        (["alpha", "beta"], 0.62),
    ]):
        db.runs.append(HistoricalRun(
            profile_name=f"run-{i}",
            fingerprint=_fp(
                f"run-{i}",
                plugin_ids=sorted(pids),
                capability_coverage=sorted(set(pids)),
                n_plugins=len(pids),
            ),
            overall_score=score,
            per_task_score={"task-A": score},
        ))
    return db


def test_generate_recommendations_suggests_adding_strong_plugin():
    db = _seed_database()
    our_fp = _fp(
        "candidate",
        plugin_ids=["alpha"],
        capability_coverage=["alpha"],
        n_plugins=1,
    )
    recs = generate_recommendations(
        fingerprint=our_fp,
        db=db,
        factor=analyze(db),
        utilization=None,
    )
    assert recs.recommendations, "expected recommendations"
    targets = {r.target for r in recs.recommendations if r.kind == "add_plugin"}
    assert "magic" in targets


def test_generate_recommendations_empty_when_db_too_small():
    db = HistoricalDatabase()
    our_fp = _fp("candidate")
    recs = generate_recommendations(
        fingerprint=our_fp, db=db, factor=None, utilization=None
    )
    assert recs.recommendations == []
    assert "historical database" in recs.note


def test_attribute_surprise_names_missing_capability_on_negative_delta():
    db = _seed_database()
    our_fp = _fp(
        "candidate",
        plugin_ids=["alpha"],
        capability_coverage=["alpha"],
    )
    cause = attribute_surprise(our_fp, "task-A", -0.3, db)
    # High scorers all have 'magic'; we don't — should be named
    assert "magic" in cause


def test_plugin_leaderboard_ranks_by_delta():
    db = _seed_database()
    leaderboard = compute_plugin_leaderboard(db, min_sample=2)
    assert leaderboard
    top = leaderboard[0]
    # 'magic' should be on top with the highest positive delta
    assert top.plugin_id == "magic"
    assert top.impact_delta > 0


def test_capability_gaps_detects_missing_threshold(tmp_path):
    db = HistoricalDatabase()
    db.runs.append(HistoricalRun(
        profile_name="A",
        fingerprint=_fp("A"),
        overall_score=0.4,
        per_task_score={"hard-task": 0.3, "easy-task": 0.9},
    ))
    db.runs.append(HistoricalRun(
        profile_name="B",
        fingerprint=_fp("B"),
        overall_score=0.5,
        per_task_score={"hard-task": 0.4, "easy-task": 0.95},
    ))
    gaps = compute_capability_gaps(db, threshold=0.7)
    gap_ids = {g.capability for g in gaps}
    assert "hard-task" in gap_ids
    assert "easy-task" not in gap_ids


def test_publish_insights_writes_all_files(tmp_path):
    db = _seed_database()
    written = publish_insights(db, tmp_path)
    for name in (
        "plugin_leaderboard",
        "factor_importance",
        "interactions",
        "gaps",
        "calibration",
        "summary",
    ):
        assert name in written, name
        assert written[name].exists()
    # Summary file should be valid JSON
    data = json.loads((tmp_path / "summary.json").read_text())
    assert data["n_runs"] == len(db)


# ---------------------------------------------------------------------------
# End-to-end diagnostic with every new section
# ---------------------------------------------------------------------------


def test_build_diagnostic_populates_v05_sections(tmp_path):
    db = _seed_database()
    # Insert a few more runs so the factor analysis activates
    for i in range(3):
        db.runs.append(HistoricalRun(
            profile_name=f"extra-{i}",
            fingerprint=_fp(
                f"extra-{i}",
                plugin_ids=sorted(["alpha", "magic"]),
                capability_coverage=sorted(["alpha", "magic"]),
                n_plugins=2,
            ),
            overall_score=0.9,
            per_task_score={"task-A": 0.9},
        ))

    profile = _make_profile("candidate", ["alpha"])
    manifests = {"alpha": _make_manifest("alpha", tools=["alpha_tool"])}
    traces = {
        "alpha": RegistrationTrace(
            plugin_id="alpha",
            tools=["alpha_tool"],
            tool_families_seen=["read"],
        )
    }
    transcripts = {
        "task-A": _make_transcript([("alpha_tool", "read")]),
    }

    report = build_diagnostic(
        profile=profile,
        manifests=manifests,
        db=db,
        actual_overall_score=0.55,
        actual_per_task_scores={"task-A": 0.55},
        transcripts=transcripts,
        traces=traces,
        tier_of={"task-A": "tier1"},
    )

    assert report.calibration_error is not None
    assert report.robustness_profile is not None
    assert report.utilization is not None
    assert report.manifest_reality is not None
    assert report.recommendations is not None
    assert report.fingerprint_hash
    assert report.robustness_profile.n_tasks == 1
    assert report.utilization.n_plugins == 1
    text = report.render_text()
    assert "Robustness Profile" in text
    assert "Plugin Utilization Audit" in text
    # Recommendations might be empty but the section header/note should
    # appear either way; we just check the report renders without error.
    assert "Configuration Diagnostic" in text


def test_submit_run_records_prediction_for_calibration_tracking(tmp_path):
    db_path = tmp_path / "runs.json"
    db = HistoricalDatabase(path=db_path)
    for i in range(5):
        db.runs.append(HistoricalRun(
            profile_name=f"seed-{i}",
            fingerprint=_fp(
                f"seed-{i}",
                plugin_ids=["alpha"],
                capability_coverage=["alpha"],
                n_plugins=1,
            ),
            overall_score=0.7,
            per_task_score={"t": 0.7},
            predicted_score_at_submission=0.65,
        ))

    profile = _make_profile("new", ["alpha"])
    manifests = {"alpha": _make_manifest("alpha", tools=["tool"])}

    report = submit_run(
        profile=profile,
        manifests=manifests,
        db=db,
        actual_overall_score=0.72,
        actual_per_task_scores={"t": 0.72},
    )
    # The brand-new run should be recorded with its prediction snapshot
    latest = db.runs[-1]
    assert latest.profile_name == "new"
    assert latest.predicted_score_at_submission is not None
    assert report.calibration_error is not None
