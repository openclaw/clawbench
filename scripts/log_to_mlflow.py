#!/usr/bin/env python3
"""Log a ClawBench BenchmarkResult to MLflow.

Standalone script -- not imported by the clawbench package.
Requires: pip install mlflow  (or pip install clawbench[mlflow])

Usage:
    python scripts/log_to_mlflow.py /results/benchmark.json

Environment:
    MLFLOW_TRACKING_URI      MLflow tracking server (default: http://localhost:5000)
    MLFLOW_EXPERIMENT_NAME   Experiment name (default: clawbench)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(result_path: str) -> None:
    try:
        import mlflow
    except ImportError:
        print(
            "mlflow is not installed. Install with: pip install mlflow"
            "  (or pip install clawbench[mlflow])",
            file=sys.stderr,
        )
        sys.exit(1)

    from clawbench.schemas import BenchmarkResult

    with open(result_path, encoding="utf-8") as f:
        result = BenchmarkResult(**json.load(f))

    experiment_id = os.environ.get("MLFLOW_EXPERIMENT_ID")
    if experiment_id:
        experiment = mlflow.set_experiment(experiment_id=experiment_id)
    else:
        experiment = mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT_NAME", "clawbench"))

    run_name = f"{result.model}-{result.submission_id[:8]}"
    with mlflow.start_run(run_name=run_name):
        mlflow.log_params(
            {
                "model": result.model,
                "provider": result.provider,
                "benchmark_version": result.benchmark_version,
                "openclaw_version": result.openclaw_version or "unknown",
                "judge_model": result.judge_model or "none",
                "task_snapshot_fingerprint": result.task_snapshot_fingerprint or "unknown",
            }
        )

        mlflow.log_metrics(
            {
                "overall_score": result.overall_score,
                "overall_completion": result.overall_completion,
                "overall_trajectory": result.overall_trajectory,
                "overall_behavior": result.overall_behavior,
                "overall_reliability": result.overall_reliability,
                "overall_pass_hat_k": result.overall_pass_hat_k,
                "overall_judge_score": result.overall_judge_score,
                "overall_judge_confidence": result.overall_judge_confidence,
                "overall_judge_pass_rate": result.overall_judge_pass_rate,
                "judge_task_coverage": result.judge_task_coverage,
                "overall_weighted_query_score": result.overall_weighted_query_score,
                "overall_median_latency_ms": result.overall_median_latency_ms,
                "overall_p95_latency_ms": result.overall_p95_latency_ms,
                "overall_total_tokens": result.overall_total_tokens,
                "overall_cost_usd": result.overall_cost_usd,
                "overall_tokens_per_pass": result.overall_tokens_per_pass,
                "overall_cost_per_pass": result.overall_cost_per_pass,
                "overall_ci_lower": result.overall_ci_lower,
                "overall_ci_upper": result.overall_ci_upper,
            }
        )

        for tier in result.tier_results:
            mlflow.log_metrics(
                {
                    f"{tier.tier}/score": tier.mean_task_score,
                    f"{tier.tier}/completion": tier.mean_completion,
                    f"{tier.tier}/trajectory": tier.mean_trajectory,
                    f"{tier.tier}/behavior": tier.mean_behavior,
                    f"{tier.tier}/reliability": tier.mean_reliability,
                }
            )

        for i, task in enumerate(result.task_results):
            mlflow.log_metrics(
                {
                    f"task/{task.task_id}/score": task.mean_task_score,
                    f"task/{task.task_id}/reliability": task.reliability_score,
                },
                step=i,
            )

        mlflow.set_tags(
            {
                "submission_id": result.submission_id,
                "timestamp": result.timestamp,
                "certified": str(result.certified),
            }
        )

        try:
            mlflow.log_artifact(result_path)
        except Exception as e:
            print(f"Warning: artifact upload failed: {e}", file=sys.stderr)
            print("Metrics and params were logged successfully.", file=sys.stderr)

    print(f"Logged to MLflow: experiment={experiment.name} run={run_name}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <result.json>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
