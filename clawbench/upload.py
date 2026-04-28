"""Upload benchmark results to a Hugging Face Dataset.

Each submission is written as its own parquet shard. This avoids the
read-modify-write race caused by rewriting the single `submissions`
split file for every completed job.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path

from clawbench.hub import ensure_dataset_repo, resolve_dataset_repo
from clawbench.schemas import BenchmarkResult

logger = logging.getLogger(__name__)


async def upload_result(
    result: BenchmarkResult,
    dataset_repo: str | None = None,
    token: str | None = None,
) -> str:
    hf_token = token or os.environ.get("HF_TOKEN", "")
    if not hf_token:
        raise RuntimeError("HF_TOKEN not set. Get a token at https://huggingface.co/settings/tokens")
    resolved_repo = dataset_repo or resolve_dataset_repo(hf_token)

    try:
        from datasets import Dataset
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise RuntimeError("Install 'datasets' and 'huggingface_hub' to upload results") from exc

    row = {
        "submission_id": result.submission_id,
        "model": result.model,
        "provider": result.provider,
        "timestamp": result.timestamp,
        "openclaw_version": result.openclaw_version,
        "benchmark_version": result.benchmark_version,
        "overall_score": result.overall_score,
        "overall_completion": result.overall_completion,
        "overall_trajectory": result.overall_trajectory,
        "overall_behavior": result.overall_behavior,
        "judge_model": result.judge_model,
        "overall_judge_score": result.overall_judge_score,
        "overall_judge_confidence": result.overall_judge_confidence,
        "overall_judge_pass_rate": result.overall_judge_pass_rate,
        "judge_task_coverage": result.judge_task_coverage,
        "judge_error_count": result.judge_error_count,
        "overall_reliability": result.overall_reliability,
        "overall_weighted_query_score": result.overall_weighted_query_score,
        "overall_median_latency_ms": result.overall_median_latency_ms,
        "overall_p95_latency_ms": result.overall_p95_latency_ms,
        "overall_total_tokens": result.overall_total_tokens,
        "overall_cost_usd": result.overall_cost_usd,
        "overall_tokens_per_pass": result.overall_tokens_per_pass,
        "overall_cost_per_pass": result.overall_cost_per_pass,
        "consensus_subset_score": result.consensus_subset_score,
        "hard_subset_score": result.hard_subset_score,
        "public_dev_score": result.public_dev_score,
        "official_hidden_score": result.official_hidden_score,
        "clear_prompt_score": result.clear_prompt_score,
        "ambiguous_prompt_score": result.ambiguous_prompt_score,
        "overall_delivery_outcome_counts": _json_column(result.overall_delivery_outcome_counts),
        "overall_failure_mode_counts": _json_column(result.overall_failure_mode_counts),
        "overall_pass_hat_k": result.overall_pass_hat_k,
        "overall_ci_lower": result.overall_ci_lower,
        "overall_ci_upper": result.overall_ci_upper,
        "certified": result.certified,
        "environment_checksum": result.environment_checksum,
        "environment": _json_column(result.environment),
        "tier_scores": _json_column({
            tier_result.tier: {
                "mean_task_score": tier_result.mean_task_score,
                "mean_completion": tier_result.mean_completion,
                "mean_trajectory": tier_result.mean_trajectory,
                "mean_behavior": tier_result.mean_behavior,
                "mean_judge": tier_result.mean_judge,
                "mean_reliability": tier_result.mean_reliability,
                "ci_lower": tier_result.ci_lower,
                "ci_upper": tier_result.ci_upper,
            }
            for tier_result in result.tier_results
        }),
        "scenario_scores": _json_column({
            scenario_result.scenario: {
                "mean_task_score": scenario_result.mean_task_score,
                "weighted_score": scenario_result.weighted_score,
                "mean_completion": scenario_result.mean_completion,
                "mean_trajectory": scenario_result.mean_trajectory,
                "mean_behavior": scenario_result.mean_behavior,
                "mean_judge": scenario_result.mean_judge,
                "mean_reliability": scenario_result.mean_reliability,
                "pass_hat_k_rate": scenario_result.pass_hat_k_rate,
                "total_weight": scenario_result.total_weight,
            }
            for scenario_result in result.scenario_results
        }),
        "task_results": _json_column([
            {
                "task_id": task.task_id,
                "tier": task.tier,
                "family": task.family,
                "scenario": task.scenario,
                "subscenario": task.subscenario,
                "artifact_type": task.artifact_type,
                "prompt_variant": task.prompt_variant,
                "query_difficulty": task.query_difficulty,
                "query_weight": task.query_weight,
                "pool": task.pool,
                "subsets": task.subsets,
                "capabilities": task.capabilities,
                "mean_task_score": task.mean_task_score,
                "mean_run_score": task.mean_run_score,
                "mean_completion_score": task.mean_completion_score,
                "mean_trajectory_score": task.mean_trajectory_score,
                "mean_behavior_score": task.mean_behavior_score,
                "mean_judge_score": task.mean_judge_score,
                "mean_judge_confidence": task.mean_judge_confidence,
                "judge_pass_rate": task.judge_pass_rate,
                "judged_runs": task.judged_runs,
                "judge_error_count": task.judge_error_count,
                "reliability_score": task.reliability_score,
                "variance_score": task.variance_score,
                "median_duration_ms": task.median_duration_ms,
                "p95_duration_ms": task.p95_duration_ms,
                "mean_total_tokens": task.mean_total_tokens,
                "mean_cost_usd": task.mean_cost_usd,
                "tokens_per_pass": task.tokens_per_pass,
                "cost_per_pass": task.cost_per_pass,
                "worst_of_n": task.worst_of_n,
                "delivery_outcome_counts": task.delivery_outcome_counts,
                "failure_mode_counts": task.failure_mode_counts,
                "pass_at_1": task.pass_at_1,
                "pass_rate": task.pass_rate,
                "pass_hat_k": task.pass_hat_k,
                "runs": task.runs,
            }
            for task in result.task_results
        ]),
    }

    api = HfApi(token=hf_token)
    ensure_dataset_repo(api, resolved_repo)

    ds = Dataset.from_list([row])
    shard_name = _submission_shard_name(result.submission_id)
    with tempfile.TemporaryDirectory(prefix="clawbench-upload-") as tmp_dir:
        local_path = Path(tmp_dir) / shard_name
        ds.to_parquet(str(local_path))
        api.upload_file(
            path_or_fileobj=str(local_path),
            path_in_repo=f"data/submissions/{shard_name}",
            repo_id=resolved_repo,
            repo_type="dataset",
        )
    url = f"https://huggingface.co/datasets/{resolved_repo}"
    logger.info(
        "Result uploaded to %s as append-only shard %s",
        url,
        shard_name,
    )
    return url


def _submission_shard_name(submission_id: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", submission_id.strip()).strip(".-")
    return f"{safe_id or 'submission'}.parquet"


def _json_column(value: object) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))
