"""ClawBench v0.5 — Ecosystem Insights publisher.

After enough submissions accumulate, ClawBench publishes ecosystem-level
insights derived from the historical fingerprint database
(CLAWBENCH_V0_4_SPEC.md v0.5 §"Community Insights"):

  - Plugin impact leaderboard
  - Strongest interactions
  - Overhyped plugins (would require ClawHub install counts — stubbed)
  - Underrated plugins (same)
  - Capability gaps across task families

This module computes those insights and writes them to the `insights/`
directory as JSON so they can be consumed by the web UI or by plugin
authors via API.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path

from clawbench.factor_analysis import FactorAnalysisReport, analyze
from clawbench.prediction import HistoricalDatabase


@dataclass
class PluginImpactEntry:
    plugin_id: str
    n_profiles_with: int
    n_profiles_without: int
    mean_with: float
    mean_without: float
    impact_delta: float  # mean_with - mean_without
    confidence: float  # 0..1 scaled by min sample size on either side

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CapabilityGap:
    capability: str
    best_score_observed: float
    n_profiles_attempted: int
    threshold: float
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def compute_plugin_leaderboard(
    db: HistoricalDatabase, *, min_sample: int = 2
) -> list[PluginImpactEntry]:
    """Average score delta when each plugin is added to comparable profiles.

    Simplest valid definition: for each plugin id appearing in at least
    `min_sample` profiles, compute the mean overall score of runs that
    include the plugin vs runs that do not. Report the delta ordered by
    magnitude.

    This is confounded by other factors, but at the scale of 30+
    submissions the ranking is usable, and the confidence column makes
    the small-sample entries visibly less trustworthy.
    """
    if len(db) < min_sample * 2:
        return []

    all_plugin_ids: set[str] = set()
    for run in db.runs:
        all_plugin_ids.update(run.fingerprint.plugin_ids)

    entries: list[PluginImpactEntry] = []
    for pid in sorted(all_plugin_ids):
        with_scores = [
            r.overall_score for r in db.runs if pid in r.fingerprint.plugin_ids
        ]
        without_scores = [
            r.overall_score for r in db.runs if pid not in r.fingerprint.plugin_ids
        ]
        if len(with_scores) < min_sample or len(without_scores) < min_sample:
            continue
        mean_with = sum(with_scores) / len(with_scores)
        mean_without = sum(without_scores) / len(without_scores)
        min_side = min(len(with_scores), len(without_scores))
        # Confidence grows with min sample size, saturates at 0.9
        confidence = min(0.9, 0.1 + 0.04 * min_side)
        entries.append(PluginImpactEntry(
            plugin_id=pid,
            n_profiles_with=len(with_scores),
            n_profiles_without=len(without_scores),
            mean_with=round(mean_with, 4),
            mean_without=round(mean_without, 4),
            impact_delta=round(mean_with - mean_without, 4),
            confidence=round(confidence, 2),
        ))

    entries.sort(key=lambda e: e.impact_delta, reverse=True)
    return entries


def compute_capability_gaps(
    db: HistoricalDatabase, *, threshold: float = 0.7
) -> list[CapabilityGap]:
    """Find per-task capability gaps.

    A capability gap is a task where NO profile in the database has
    scored at or above `threshold`. These are the tasks that currently
    frustrate the entire ecosystem — good signal for where benchmark
    headroom lies.
    """
    if not db.runs:
        return []

    task_best: dict[str, float] = {}
    task_attempts: Counter[str] = Counter()
    for run in db.runs:
        for task_id, score in run.per_task_score.items():
            task_attempts[task_id] += 1
            if score > task_best.get(task_id, -1.0):
                task_best[task_id] = score

    gaps: list[CapabilityGap] = []
    for task_id, best in sorted(task_best.items()):
        if best < threshold:
            gaps.append(CapabilityGap(
                capability=task_id,
                best_score_observed=round(best, 4),
                n_profiles_attempted=task_attempts[task_id],
                threshold=threshold,
                note=f"best observed {best:.3f} < threshold {threshold:.2f}",
            ))
    gaps.sort(key=lambda g: g.best_score_observed)
    return gaps


def publish_insights(
    db: HistoricalDatabase,
    output_dir: Path,
    *,
    factor_report: FactorAnalysisReport | None = None,
    threshold: float = 0.7,
) -> dict[str, Path]:
    """Compute and write all ecosystem insight files.

    Returns a mapping of insight name → file path written.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    # 1) plugin_leaderboard.json
    leaderboard = compute_plugin_leaderboard(db)
    path = output_dir / "plugin_leaderboard.json"
    path.write_text(
        json.dumps([e.to_dict() for e in leaderboard], indent=2),
        encoding="utf-8",
    )
    written["plugin_leaderboard"] = path

    # 2) interactions.json + factor_importance.json
    if factor_report is None:
        factor_report = analyze(db)
    path = output_dir / "factor_importance.json"
    path.write_text(
        json.dumps(
            {
                "n_runs": factor_report.n_runs,
                "method": factor_report.method,
                "total_variance": factor_report.total_variance,
                "main_effects": [m.to_dict() for m in factor_report.main_effects],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    written["factor_importance"] = path

    path = output_dir / "interactions.json"
    path.write_text(
        json.dumps(
            [i.to_dict() for i in factor_report.interactions],
            indent=2,
        ),
        encoding="utf-8",
    )
    written["interactions"] = path

    # 3) gaps.json
    gaps = compute_capability_gaps(db, threshold=threshold)
    path = output_dir / "gaps.json"
    path.write_text(
        json.dumps([g.to_dict() for g in gaps], indent=2),
        encoding="utf-8",
    )
    written["gaps"] = path

    # 4) calibration.json — how well have predictions matched reality
    path = output_dir / "calibration.json"
    path.write_text(
        json.dumps(db.calibration_metrics(), indent=2),
        encoding="utf-8",
    )
    written["calibration"] = path

    # 5) summary.json — top-level pointers
    summary = {
        "n_runs": len(db),
        "leaderboard_top": [e.to_dict() for e in leaderboard[:5]],
        "top_interactions": [i.to_dict() for i in factor_report.interactions[:5]],
        "n_capability_gaps": len(gaps),
        "factor_method": factor_report.method,
    }
    path = output_dir / "summary.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    written["summary"] = path

    return written
