#!/usr/bin/env python3
"""Decompose posterior run_score variance into seed noise and capability signal.

Each task has repeated runs per model.

    sigma^2_seed(task, model) = variance across repeated runs for one model
    sigma^2_capability(task)  = variance across model means for that task

Signal-to-noise ratio per task:

    SNR(task) = sigma^2_capability / mean_model sigma^2_seed

High SNR means cross-model differences are likely real. Low SNR means the
benchmark signal is dominated by run-to-run variance rather than capability.

Aggregate decomposition:

    total_var = mean_task seed_var + mean_task cap_var
    capability_fraction = mean_task cap_var / total_var

This script keeps the posterior/archive-based workflow used by the current
pipeline, but the statistical meaning is the same as the earlier analysis.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, variance

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawbench.dynamics_archive import load_task_runs_by_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Variance decomposition on cached runs")
    parser.add_argument("--archive-dir", type=Path, default=Path(".clawbench/run_cache"))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--tier", choices=["tier1", "tier2", "tier3", "tier4", "tier5"], default=None)
    args = parser.parse_args()

    grouped = load_task_runs_by_model(args.archive_dir, tier=args.tier)
    if not grouped:
        raise SystemExit(f"No cached runs found under {args.archive_dir}")

    # Collect repeated run scores as {task -> {model -> [run_scores]}}.
    scores: dict[str, dict[str, list[float]]] = defaultdict(dict)
    for model_name, task_runs in grouped.items():
        for task_id, runs in task_runs.items():
            vals = [float(run.run_score) for run in runs]
            if vals:
                scores[task_id][model_name] = vals

    task_stats = []
    for task_id, per_model in scores.items():
        model_vars = []
        model_means = []
        for runs in per_model.values():
            if len(runs) >= 2:
                model_vars.append(variance(runs))
            if runs:
                model_means.append(mean(runs))

        # Mean within-model variance is the seed-noise term.
        mean_seed_var = mean(model_vars) if model_vars else 0.0
        # Variance of model means is the capability-signal term.
        cap_var = variance(model_means) if len(model_means) >= 2 else 0.0
        snr = cap_var / (mean_seed_var + 1e-9)
        task_stats.append(
            {
                "task": task_id,
                "seed_var": float(mean_seed_var),
                "cap_var": float(cap_var),
                "snr": float(snr),
                "n_models": len(model_means),
                "limited_model_diversity": len(model_means) < 2,
            }
        )

    task_stats.sort(key=lambda row: row["snr"], reverse=True)
    if not task_stats:
        raise SystemExit("No task-level scores found in archive.")

    # Aggregate over tasks to estimate how much of benchmark variance is real
    # capability signal versus run-to-run noise.
    total_seed = mean(row["seed_var"] for row in task_stats)
    total_cap = mean(row["cap_var"] for row in task_stats)
    total = total_seed + total_cap
    capability_fraction = total_cap / total if total > 1e-12 else 0.0

    # Coarse SNR buckets help downstream reporting and task weighting.
    high_snr = [row for row in task_stats if row["snr"] >= 5]
    mid_snr = [row for row in task_stats if 1 <= row["snr"] < 5]
    low_snr = [row for row in task_stats if row["snr"] < 1]

    out = {
        "per_task": task_stats,
        "aggregate": {
            "mean_seed_var": float(total_seed),
            "mean_cap_var": float(total_cap),
            "capability_fraction": float(capability_fraction),
            "high_snr_tasks": len(high_snr),
            "mid_snr_tasks": len(mid_snr),
            "low_snr_tasks": len(low_snr),
        },
    }

    args.reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.reports_dir / "variance_decomposition.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
