#!/usr/bin/env python3
"""SNR x |C(q)| weighted ranking from posterior cached runs.

Weighted headline score:

    w(q) = max(0, SNR(q)) * |C(q)|
    score(model) = sum_q w(q) * mean_run_score(model, q) / sum_q w(q)

We also report:

    snr_only              = SNR-weighted mean
    snr_x_abs_cq          = SNR x |C(q)| weighted mean
    snr_x_abs_cq_winsorized = same, but top task weights are clamped at p95

This keeps noisy low-SNR tasks from dominating and upweights tasks whose
response geometry suggests a stronger capability signal.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawbench.dynamics_archive import load_task_runs_by_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute SNR-weighted posterior model ranking")
    parser.add_argument("--archive-dir", type=Path, default=Path(".clawbench/run_cache"))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--tier", choices=["tier1", "tier2", "tier3", "tier4", "tier5"], default=None)
    args = parser.parse_args()

    cq_path = args.reports_dir / "constraint_index.json"
    var_path = args.reports_dir / "variance_decomposition.json"
    if not cq_path.exists() or not var_path.exists():
        raise SystemExit("Missing prerequisite reports: run compute_constraint_index.py and variance_decomp.py first.")

    cq = json.loads(cq_path.read_text(encoding="utf-8"))
    var = json.loads(var_path.read_text(encoding="utf-8"))
    snr_by_task = {row["task"]: row["snr"] for row in var.get("per_task", [])}

    grouped = load_task_runs_by_model(args.archive_dir, tier=args.tier)
    if not grouped:
        raise SystemExit(f"No cached runs found under {args.archive_dir}")

    per_model_task_scores: dict[str, dict[str, list[float]]] = defaultdict(dict)
    for model_name, task_runs in grouped.items():
        for task_id, runs in task_runs.items():
            per_model_task_scores[model_name][task_id] = [float(run.run_score) for run in runs]

    per_model_task_mean = {
        model_name: {
            task_id: mean(vals)
            for task_id, vals in task_scores.items()
            if vals
        }
        for model_name, task_scores in per_model_task_scores.items()
    }

    common_tasks = sorted(set(cq) & set(snr_by_task))
    if not common_tasks:
        raise SystemExit("No overlap between constraint_index and variance_decomposition task sets.")

    weights = {task: max(0.0, snr_by_task[task]) * abs(cq[task].get("C_q", 0.0)) for task in common_tasks}
    snr_weights = {task: max(0.0, snr_by_task[task]) for task in common_tasks}

    w95 = float(np.percentile(list(weights.values()), 95)) if weights else 0.0
    winsorized = {task: min(weight, w95) for task, weight in weights.items()}

    w_sum = sum(weights.values())
    snr_sum = sum(snr_weights.values())
    wins_sum = sum(winsorized.values())

    results = []
    for model_name, task_means in per_model_task_mean.items():
        covered = [task for task in common_tasks if task in task_means]
        if not covered:
            continue

        flat = mean(task_means[task] for task in covered)
        weighted = (
            sum(weights[task] * task_means.get(task, 0.0) for task in common_tasks) / w_sum
            if w_sum > 1e-12
            else 0.0
        )
        snr_only = (
            sum(snr_weights[task] * task_means.get(task, 0.0) for task in common_tasks) / snr_sum
            if snr_sum > 1e-12
            else 0.0
        )
        wins_score = (
            sum(winsorized[task] * task_means.get(task, 0.0) for task in common_tasks) / wins_sum
            if wins_sum > 1e-12
            else 0.0
        )

        results.append(
            {
                "model": model_name,
                "flat": float(flat),
                "snr_x_abs_cq": float(weighted),
                "snr_only": float(snr_only),
                "snr_x_abs_cq_winsorized": float(wins_score),
                "coverage": len(covered),
            }
        )

    results.sort(key=lambda row: row["snr_x_abs_cq_winsorized"], reverse=True)

    out = {
        "common_tasks": common_tasks,
        "weights_per_task": weights,
        "results": results,
    }

    out_path = args.reports_dir / "snr_weighted_ranking.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
