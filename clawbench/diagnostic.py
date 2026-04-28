"""ClawBench v0.5 — Configuration Diagnostic Report.

End-to-end glue that ties together:
  - profile.py          (parse + fingerprint a submission)
  - prediction.py       (k-NN cold-start prediction + surprise attribution)
  - factor_analysis.py  (fANOVA ecosystem insights, RF or lite)
  - utilization.py      (plugin utilization audit + manifest-vs-reality gap)
  - recommendations.py  (prescriptive profile changes)
  - stats.py            (Taguchi S/N robustness profile)
  - insights.py         (ecosystem insight file publishing)
  - existing v0.4 scoring (the deterministic ground truth)

This module is the user-facing entry point. It produces the Configuration
Diagnostic Report that distinguishes ClawBench from descriptive
leaderboards.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from clawbench.factor_analysis import FactorAnalysisReport, analyze
from clawbench.prediction import (
    HistoricalDatabase,
    HistoricalRun,
    attribute_surprise,
    predict_profile,
)
from clawbench.profile import (
    PluginManifest,
    PluginProfile,
    ProfileFingerprint,
    RegistrationTrace,
)
from clawbench.recommendations import (
    RecommendationSet,
    generate_recommendations,
)
from clawbench.schemas import Transcript
from clawbench.stats import RobustnessProfile, compute_robustness_profile
from clawbench.utilization import (
    ManifestRealityReport,
    UtilizationReport,
    audit_plugin_utilization,
    compute_manifest_reality_gap,
)


@dataclass
class Surprise:
    task_id: str
    predicted: float
    actual: float
    delta: float
    direction: str  # "positive" or "negative"
    likely_cause: str = ""


@dataclass
class DiagnosticReport:
    profile_name: str
    base_model: str
    fingerprint_hash: str
    overall_score: float | None
    predicted_score: float
    prediction_confidence: float
    calibration_error: float | None  # |actual - predicted| when both known
    n_neighbors_used: int
    neighbor_names: list[str]
    surprises: list[Surprise]
    capability_attributions: dict[str, float]
    factor_analysis: FactorAnalysisReport | None
    fingerprint_summary: dict[str, Any]
    robustness_profile: RobustnessProfile | None
    utilization: UtilizationReport | None
    manifest_reality: ManifestRealityReport | None
    recommendations: RecommendationSet | None
    calibration_history: dict[str, Any]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_name": self.profile_name,
            "base_model": self.base_model,
            "fingerprint_hash": self.fingerprint_hash,
            "overall_score": self.overall_score,
            "predicted_score": self.predicted_score,
            "prediction_confidence": self.prediction_confidence,
            "calibration_error": self.calibration_error,
            "n_neighbors_used": self.n_neighbors_used,
            "neighbor_names": self.neighbor_names,
            "surprises": [asdict(s) for s in self.surprises],
            "capability_attributions": self.capability_attributions,
            "factor_analysis": self.factor_analysis.to_dict() if self.factor_analysis else None,
            "fingerprint_summary": self.fingerprint_summary,
            "robustness_profile": self.robustness_profile.to_dict() if self.robustness_profile else None,
            "utilization": self.utilization.to_dict() if self.utilization else None,
            "manifest_reality": self.manifest_reality.to_dict() if self.manifest_reality else None,
            "recommendations": self.recommendations.to_dict() if self.recommendations else None,
            "calibration_history": self.calibration_history,
            "notes": self.notes,
        }

    def render_text(self) -> str:
        """Render a human-readable text report."""
        lines = []
        lines.append("═" * 70)
        lines.append(f"  ClawBench Configuration Diagnostic: {self.profile_name}")
        lines.append("═" * 70)
        lines.append("")
        lines.append(f"  Base model:        {self.base_model}")
        lines.append(f"  Fingerprint hash:  {self.fingerprint_hash}")
        if self.overall_score is not None:
            lines.append(f"  Actual score:      {self.overall_score:.3f}")
        lines.append(
            f"  Predicted score:   {self.predicted_score:.3f}  "
            f"(confidence {self.prediction_confidence:.2f})"
        )
        if self.calibration_error is not None:
            lines.append(f"  Calibration error: {self.calibration_error:+.3f}")
        if self.n_neighbors_used:
            lines.append(
                f"  Based on {self.n_neighbors_used} similar profiles: "
                f"{', '.join(self.neighbor_names[:5])}"
            )
        lines.append("")

        # Fingerprint summary
        lines.append("─ Plugin Profile Fingerprint " + "─" * 40)
        for k, v in self.fingerprint_summary.items():
            if isinstance(v, list):
                v_str = ", ".join(v) if v else "(none)"
            else:
                v_str = str(v)
            lines.append(f"  {k:24} {v_str}")
        lines.append("")

        # Robustness profile (Taguchi)
        if self.robustness_profile and self.robustness_profile.n_tasks:
            rp = self.robustness_profile
            lines.append("─ Robustness Profile (Taguchi S/N) " + "─" * 34)
            lines.append(
                f"  Mean  {rp.mean:.3f}   Worst  {rp.worst_of_n:.3f}   "
                f"Best  {rp.best_of_n:.3f}   σ  {rp.stddev:.3f}"
            )
            lines.append(
                f"  S/N ratio (larger-is-better):  {rp.sn_ratio_db:+.2f} dB"
            )
            if rp.tier_means:
                lines.append("  Per-tier means:")
                for tier, mean in sorted(rp.tier_means.items()):
                    lines.append(f"    {tier:12} {mean:.3f}")
            lines.append("")

        # Plugin Utilization Audit
        if self.utilization is not None:
            u = self.utilization
            lines.append("─ Plugin Utilization Audit " + "─" * 42)
            lines.append(
                f"  {u.n_invoked}/{u.n_plugins} plugins invoked "
                f"({u.utilization_rate:.0%})   "
                f"dead weight: {u.n_dead_weight}   "
                f"unassigned calls: {u.unassigned_tool_calls}"
            )
            for p in u.per_plugin:
                marker = "✓" if p.invoked else "·"
                status = f"{p.invocation_count:>4} calls" if p.invoked else "DEAD WEIGHT"
                lines.append(
                    f"  {marker} {p.plugin_id:36} {status:14}  "
                    f"{len(p.task_ids_with_invocation)} tasks"
                )
            lines.append("")

        # Manifest vs Reality gap
        if self.manifest_reality and self.manifest_reality.per_plugin:
            lines.append("─ Manifest vs Reality Gap " + "─" * 43)
            for g in self.manifest_reality.per_plugin:
                lines.append(
                    f"  {g.plugin_id:30}  coverage {g.claim_coverage:.0%}"
                )
                if g.unused_capabilities:
                    lines.append(
                        f"    ├─ claimed but unused: {', '.join(g.unused_capabilities)}"
                    )
                if g.unclaimed_capabilities:
                    lines.append(
                        f"    └─ observed but not in manifest: {', '.join(g.unclaimed_capabilities)}"
                    )
            lines.append("")

        # Surprises with cause attribution
        if self.surprises:
            lines.append("─ Surprises (predicted vs actual) " + "─" * 36)
            for s in self.surprises[:10]:
                arrow = "↑" if s.direction == "positive" else "↓"
                lines.append(
                    f"  {arrow} {s.task_id:40}  predicted {s.predicted:.2f}  "
                    f"actual {s.actual:.2f}  Δ {s.delta:+.2f}"
                )
                if s.likely_cause:
                    lines.append(f"      → {s.likely_cause}")
            lines.append("")

        # Capability attributions
        if self.capability_attributions:
            lines.append("─ Capability Attributions " + "─" * 44)
            sorted_attrs = sorted(
                self.capability_attributions.items(),
                key=lambda x: abs(x[1]),
                reverse=True,
            )
            for cap, delta in sorted_attrs[:10]:
                sign = "+" if delta >= 0 else ""
                lines.append(f"  {cap:40}  {sign}{delta:.3f}")
            lines.append("")

        # Recommendations (the prescriptive output)
        if self.recommendations and self.recommendations.recommendations:
            lines.append("─ Recommendations " + "─" * 51)
            for rec in self.recommendations.recommendations:
                delta_sign = "+" if rec.estimated_delta >= 0 else ""
                lines.append(
                    f"  [{rec.kind}] {rec.target}"
                )
                lines.append(
                    f"      Δ {delta_sign}{rec.estimated_delta:.3f}  "
                    f"confidence {rec.confidence:.2f}"
                )
                lines.append(f"      reason: {rec.rationale}")
                for ev in rec.evidence[:3]:
                    lines.append(f"      • {ev}")
            lines.append("")
        elif self.recommendations and self.recommendations.note:
            lines.append("─ Recommendations " + "─" * 51)
            lines.append(f"  {self.recommendations.note}")
            lines.append("")

        # Factor analysis (ecosystem-level)
        if self.factor_analysis and self.factor_analysis.main_effects:
            header = (
                f"─ Ecosystem Factor Analysis "
                f"[{self.factor_analysis.method}] "
                f"({self.factor_analysis.n_runs} runs) "
            )
            lines.append(header + "─" * max(0, 70 - len(header)))
            for me in self.factor_analysis.main_effects[:10]:
                bar = "█" * int(me.importance * 30)
                lines.append(
                    f"  {me.feature:36}  {bar:30}  {me.importance:.3f}  (Δ {me.delta:+.2f})"
                )
            if self.factor_analysis.interactions:
                lines.append("")
                lines.append("  Strongest interactions:")
                for inter in self.factor_analysis.interactions:
                    lines.append(
                        f"    {inter.feature_a} × {inter.feature_b}  →  "
                        f"residual {inter.interaction_strength:+.3f}"
                    )
            lines.append("")

        # Calibration history
        if self.calibration_history and self.calibration_history.get("n", 0) > 0:
            ch = self.calibration_history
            lines.append("─ Calibration History " + "─" * 47)
            lines.append(
                f"  n={ch['n']}   MAE {ch['mae']:.3f}   "
                f"RMSE {ch['rmse']:.3f}   bias {ch['bias']:+.3f}"
            )
            if ch.get("mae_target_met"):
                lines.append("  ✓ v0.5 success criterion met (MAE < 0.08 at n≥100)")
            lines.append("")

        if self.notes:
            lines.append("─ Notes " + "─" * 60)
            for n in self.notes:
                lines.append(f"  • {n}")
            lines.append("")

        lines.append("═" * 70)
        return "\n".join(lines)


SURPRISE_THRESHOLD = 0.15


def build_diagnostic(
    profile: PluginProfile,
    manifests: dict[str, PluginManifest],
    db: HistoricalDatabase,
    actual_overall_score: float | None = None,
    actual_per_task_scores: dict[str, float] | None = None,
    traces: dict[str, RegistrationTrace] | None = None,
    transcripts: dict[str, Transcript] | None = None,
    tier_of: dict[str, str] | None = None,
    enable_factor_analysis: bool = True,
) -> DiagnosticReport:
    """Build a diagnostic report for a Plugin Profile.

    Parameters
    ----------
    profile, manifests, db, actual_*, traces
        Same as before.
    transcripts : dict[task_id, Transcript] | None
        Per-task transcripts captured by the harness. Required for the
        Plugin Utilization Audit and the Manifest-vs-Reality Gap; both
        sections are omitted when transcripts are absent.
    tier_of : dict[task_id, tier_name] | None
        Optional task → tier mapping used to compute per-tier means in
        the robustness profile.
    enable_factor_analysis : bool
        Run factor analysis on the historical database. Default True.

    If `actual_*` are None, the report is purely predictive (pre-run).
    If actuals are provided, the report includes calibration error,
    surprise detection, robustness profile, and recommendations.
    """
    fingerprint = ProfileFingerprint.from_profile(profile, manifests, traces)
    prediction = predict_profile(fingerprint, db)

    surprises: list[Surprise] = []
    if actual_per_task_scores is not None:
        for task_id, predicted in prediction.predicted_per_task.items():
            actual = actual_per_task_scores.get(task_id)
            if actual is None:
                continue
            delta = actual - predicted
            if abs(delta) >= SURPRISE_THRESHOLD:
                cause = attribute_surprise(fingerprint, task_id, delta, db)
                surprises.append(Surprise(
                    task_id=task_id,
                    predicted=round(predicted, 4),
                    actual=round(actual, 4),
                    delta=round(delta, 4),
                    direction="positive" if delta > 0 else "negative",
                    likely_cause=cause,
                ))

    factor = None
    if enable_factor_analysis:
        factor = analyze(db)

    # Robustness profile (Taguchi S/N + per-tier means)
    robustness = None
    if actual_per_task_scores:
        robustness = compute_robustness_profile(
            actual_per_task_scores, tier_of=tier_of
        )

    # Plugin Utilization Audit + Manifest-vs-Reality gap
    utilization = None
    manifest_reality = None
    if transcripts:
        utilization = audit_plugin_utilization(
            profile=profile,
            transcripts=transcripts,
            manifests=manifests,
            traces=traces,
        )
        manifest_reality = compute_manifest_reality_gap(
            profile=profile,
            manifests=manifests,
            utilization=utilization,
        )

    # Recommendations
    recommendations = generate_recommendations(
        fingerprint=fingerprint,
        db=db,
        factor=factor,
        utilization=utilization,
    )

    # Calibration error for this single run (if actual provided)
    calibration_error = None
    if actual_overall_score is not None:
        calibration_error = round(
            actual_overall_score - prediction.predicted_overall_score, 4
        )

    # Running calibration history from the database
    calibration_history = db.calibration_metrics()

    notes: list[str] = []
    if len(db) < 30:
        notes.append(
            f"historical database has only {len(db)} runs — predictions are weak. "
            "Calibration improves once 30+ profiles are submitted."
        )
    if not factor or not factor.main_effects:
        notes.append("factor analysis inactive — needs ≥4 distinct profiles.")
    if transcripts is None:
        notes.append(
            "transcripts not provided — plugin utilization audit and "
            "manifest-vs-reality gap skipped."
        )

    fingerprint_summary = {
        "n_plugins": fingerprint.n_plugins,
        "n_clawhub": fingerprint.n_clawhub_plugins,
        "n_custom": fingerprint.n_custom_plugins,
        "memory_slot": fingerprint.memory_slot or "(none)",
        "context_engine_slot": fingerprint.context_engine_slot or "(none)",
        "capability_coverage": fingerprint.capability_coverage,
        "hook_footprint": fingerprint.hook_footprint,
        "tool_family_surface": fingerprint.tool_family_surface,
        "n_tools_total": fingerprint.n_tools_total,
        "n_hooks_total": fingerprint.n_hooks_total,
    }

    return DiagnosticReport(
        profile_name=profile.name,
        base_model=profile.base_model,
        fingerprint_hash=fingerprint.fingerprint_hash,
        overall_score=actual_overall_score,
        predicted_score=prediction.predicted_overall_score,
        prediction_confidence=prediction.confidence,
        calibration_error=calibration_error,
        n_neighbors_used=prediction.n_neighbors_used,
        neighbor_names=prediction.neighbor_names,
        surprises=surprises,
        capability_attributions=prediction.capability_attributions,
        factor_analysis=factor,
        fingerprint_summary=fingerprint_summary,
        robustness_profile=robustness,
        utilization=utilization,
        manifest_reality=manifest_reality,
        recommendations=recommendations,
        calibration_history=calibration_history,
        notes=notes,
    )


def submit_run(
    profile: PluginProfile,
    manifests: dict[str, PluginManifest],
    db: HistoricalDatabase,
    actual_overall_score: float,
    actual_per_task_scores: dict[str, float],
    traces: dict[str, RegistrationTrace] | None = None,
    transcripts: dict[str, Transcript] | None = None,
    tier_of: dict[str, str] | None = None,
    n_runs_contributing: int = 1,
) -> DiagnosticReport:
    """Full submission flow: build diagnostic, then add to historical DB.

    The prediction computed BEFORE the run is recorded alongside the
    actual score, so the calibration tracker can report MAE over time.
    """
    # Capture the pre-run prediction before inserting anything
    fingerprint = ProfileFingerprint.from_profile(profile, manifests, traces)
    pre_prediction = predict_profile(fingerprint, db)

    report = build_diagnostic(
        profile=profile,
        manifests=manifests,
        db=db,
        actual_overall_score=actual_overall_score,
        actual_per_task_scores=actual_per_task_scores,
        traces=traces,
        transcripts=transcripts,
        tier_of=tier_of,
    )
    db.add(HistoricalRun(
        profile_name=profile.name,
        fingerprint=fingerprint,
        overall_score=actual_overall_score,
        per_task_score=actual_per_task_scores,
        predicted_score_at_submission=pre_prediction.predicted_overall_score,
        prediction_confidence_at_submission=pre_prediction.confidence,
        n_runs_contributing=n_runs_contributing,
    ))
    return report
