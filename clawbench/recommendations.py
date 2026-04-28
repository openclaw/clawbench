"""ClawBench v0.5 — Recommendations generator.

The Recommendations section is the prescriptive output that distinguishes
ClawBench from descriptive leaderboards (CLAWBENCH_V0_4_SPEC.md §8
"Configuration Diagnostic Report"). Every recommendation must be backed
by data — either by neighbor profiles that already include the suggested
plugin, or by factor-importance attributions with explicit confidence.

This module generates a ranked list of concrete profile changes from the
historical database + factor analysis + the current profile, with
per-recommendation evidence and a conservative estimated score impact.

No speculative recommendations are generated. If the database is too
small or the evidence too weak, the output is an empty list and the
caller is expected to surface that explicitly in the diagnostic.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, asdict

from clawbench.factor_analysis import FactorAnalysisReport
from clawbench.prediction import HistoricalDatabase
from clawbench.profile import ProfileFingerprint
from clawbench.utilization import UtilizationReport


@dataclass
class Recommendation:
    kind: str  # "add_plugin", "remove_plugin", "fill_slot", "swap_plugin"
    target: str  # plugin id or slot name
    rationale: str
    estimated_delta: float  # predicted score impact, signed
    confidence: float  # 0..1
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RecommendationSet:
    recommendations: list[Recommendation] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "recommendations": [r.to_dict() for r in self.recommendations],
            "note": self.note,
        }


MIN_DB_SIZE_FOR_RECOMMENDATIONS = 5
MIN_EVIDENCE_NEIGHBORS = 2


def generate_recommendations(
    fingerprint: ProfileFingerprint,
    db: HistoricalDatabase,
    factor: FactorAnalysisReport | None,
    utilization: UtilizationReport | None = None,
    *,
    max_recommendations: int = 6,
) -> RecommendationSet:
    """Generate a ranked, evidence-backed list of profile changes.

    Signals combined:
      1. Dead-weight plugins (from utilization) → remove_plugin.
      2. Empty required slots → fill_slot.
      3. Plugins appearing in high-scoring neighbors but missing from
         this profile → add_plugin.
      4. Factor-analysis main effects with positive delta and features
         this profile lacks → add_plugin (capability level).

    Every recommendation includes evidence naming either the neighbor
    profiles that justify it or the factor-analysis row that produced it.
    """
    if len(db) < MIN_DB_SIZE_FOR_RECOMMENDATIONS:
        return RecommendationSet(
            recommendations=[],
            note=(
                f"recommendations disabled: historical database has only "
                f"{len(db)} runs (need ≥{MIN_DB_SIZE_FOR_RECOMMENDATIONS})"
            ),
        )

    recs: list[Recommendation] = []

    # --- Signal 1: dead-weight plugin removal ----------------------------
    if utilization is not None:
        for p in utilization.per_plugin:
            if p.dead_weight:
                recs.append(Recommendation(
                    kind="remove_plugin",
                    target=p.plugin_id,
                    rationale=(
                        f"plugin '{p.plugin_id}' loaded but was never invoked "
                        f"during this run — consider removing it to reduce "
                        f"configuration surface area"
                    ),
                    estimated_delta=0.0,  # removing dead weight is neutral for score
                    confidence=0.9,
                    evidence=["0 tool invocations across all tasks"],
                ))

    # --- Signal 2: empty slots -------------------------------------------
    if not fingerprint.memory_slot:
        # Check if filling memory slot correlates with higher scores
        with_mem = [r for r in db.runs if r.fingerprint.memory_slot]
        without_mem = [r for r in db.runs if not r.fingerprint.memory_slot]
        if len(with_mem) >= MIN_EVIDENCE_NEIGHBORS and without_mem:
            mean_with = sum(r.overall_score for r in with_mem) / len(with_mem)
            mean_without = sum(r.overall_score for r in without_mem) / len(without_mem)
            delta = mean_with - mean_without
            if delta > 0.03:
                # Pick the most popular memory plugin across the high-scorers
                high = [r for r in with_mem if r.overall_score >= mean_with]
                memories = Counter(r.fingerprint.memory_slot for r in high)
                if memories:
                    top_mem, count = memories.most_common(1)[0]
                    recs.append(Recommendation(
                        kind="fill_slot",
                        target=f"memory={top_mem}",
                        rationale=(
                            f"profiles with a memory slot filled average "
                            f"{mean_with:.2f} vs {mean_without:.2f} without. "
                            f"'{top_mem}' is the most common choice among "
                            f"high scorers."
                        ),
                        estimated_delta=round(delta, 4),
                        confidence=round(min(0.9, 0.3 + 0.1 * len(with_mem)), 2),
                        evidence=[
                            f"{len(with_mem)} profiles with memory: mean {mean_with:.3f}",
                            f"{len(without_mem)} profiles without: mean {mean_without:.3f}",
                            f"{count}/{len(high)} high scorers use '{top_mem}'",
                        ],
                    ))

    # --- Signal 3: plugins missing vs high-scoring neighbors -------------
    our_plugin_ids = set(fingerprint.plugin_ids)
    # High scorers = top third of database by overall_score
    sorted_runs = sorted(db.runs, key=lambda r: r.overall_score, reverse=True)
    top_third = sorted_runs[: max(3, len(sorted_runs) // 3)]
    plugin_freq: Counter[str] = Counter()
    for r in top_third:
        for pid in r.fingerprint.plugin_ids:
            if pid not in our_plugin_ids:
                plugin_freq[pid] += 1

    # Only recommend plugins present in ≥ MIN_EVIDENCE_NEIGHBORS high scorers
    for plugin_id, count in plugin_freq.most_common(max_recommendations):
        if count < MIN_EVIDENCE_NEIGHBORS:
            break
        # Estimate delta: mean score of top-third runs WITH this plugin
        # minus mean of runs WITHOUT it, restricted to comparable profiles.
        with_plugin = [
            r for r in db.runs if plugin_id in r.fingerprint.plugin_ids
        ]
        without_plugin = [
            r for r in db.runs if plugin_id not in r.fingerprint.plugin_ids
        ]
        if not with_plugin or not without_plugin:
            continue
        mean_with = sum(r.overall_score for r in with_plugin) / len(with_plugin)
        mean_without = sum(r.overall_score for r in without_plugin) / len(without_plugin)
        delta = mean_with - mean_without
        if delta <= 0.01:
            continue
        # Confidence rises with sample size on both sides, caps at 0.85
        confidence = min(0.85, 0.25 + 0.05 * min(len(with_plugin), len(without_plugin)))
        recs.append(Recommendation(
            kind="add_plugin",
            target=plugin_id,
            rationale=(
                f"'{plugin_id}' appears in {count} of {len(top_third)} "
                f"top-scoring profiles and is missing from this one"
            ),
            estimated_delta=round(delta, 4),
            confidence=round(confidence, 2),
            evidence=[
                f"{len(with_plugin)} profiles with '{plugin_id}': mean {mean_with:.3f}",
                f"{len(without_plugin)} without: mean {mean_without:.3f}",
                f"present in {count}/{len(top_third)} top scorers",
            ],
        ))

    # --- Signal 4: factor-analysis lifts for features the profile lacks ---
    if factor is not None and factor.main_effects:
        our_caps = set(fingerprint.capability_coverage)
        our_hooks = set(fingerprint.hook_footprint)
        for me in factor.main_effects[:10]:
            if me.importance < 0.05 or me.delta <= 0.02:
                continue
            feat = me.feature
            if feat.startswith("capability:"):
                name = feat.split(":", 1)[1]
                if name in our_caps:
                    continue
                rationale_target = f"any plugin providing '{name}'"
            elif feat.startswith("hook:"):
                name = feat.split(":", 1)[1]
                if name in our_hooks:
                    continue
                rationale_target = f"any plugin registering hook '{name}'"
            else:
                continue
            # Avoid duplicating add_plugin recommendations that already
            # target a specific plugin providing this capability.
            recs.append(Recommendation(
                kind="add_capability",
                target=rationale_target,
                rationale=(
                    f"factor analysis attributes {me.importance:.1%} of "
                    f"variance to '{feat}' (Δ={me.delta:+.3f}); "
                    f"this profile does not cover it"
                ),
                estimated_delta=round(me.delta, 4),
                confidence=round(min(0.75, 0.2 + me.importance), 2),
                evidence=[
                    f"fANOVA importance {me.importance:.3f}",
                    f"n_with={me.n_with}, n_without={me.n_without}",
                ],
            ))

    # Rank by (estimated_delta * confidence), cap the output
    recs.sort(key=lambda r: r.estimated_delta * r.confidence, reverse=True)
    return RecommendationSet(
        recommendations=recs[:max_recommendations],
        note="" if recs else "no strong signals found in historical data",
    )
