"""Upload benchmark results to a Hugging Face Dataset.

IMPORTANT — why this file calls `load_dataset` before `push_to_hub`:

`datasets.Dataset.push_to_hub(repo, split="submissions")` writes a single
parquet shard to `data/submissions-00000-of-00001.parquet`, REPLACING
whatever was there. If you push N submissions in sequence without
reading first, only the Nth row survives — the previous N-1 are lost.

`upload_result()` therefore:
  1. Loads the existing `submissions` split if it exists
  2. Appends the new row
  3. Deduplicates by `submission_id` (so a retried upload of the same
     run doesn't create two rows)
  4. Pushes the combined dataset as a fresh parquet shard

At ClawBench's current submission rate (1-2 concurrent jobs) the read-
then-write race window is negligible. If cross-worker concurrency ever
becomes material we should move to an actually append-only format
(e.g. write per-submission parquet shards under `data/submission-<id>-
of-NNNNN.parquet` instead of overwriting a single shard).
"""

from __future__ import annotations

import logging
import os

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
        "overall_component_tokens": result.overall_component_tokens,
        "overall_total_tokens": result.overall_total_tokens,
        "overall_cost_usd": result.overall_cost_usd,
        "overall_component_tokens_per_pass": result.overall_component_tokens_per_pass,
        "overall_tokens_per_pass": result.overall_tokens_per_pass,
        "overall_cost_per_pass": result.overall_cost_per_pass,
        "consensus_subset_score": result.consensus_subset_score,
        "hard_subset_score": result.hard_subset_score,
        "public_dev_score": result.public_dev_score,
        "official_hidden_score": result.official_hidden_score,
        "clear_prompt_score": result.clear_prompt_score,
        "ambiguous_prompt_score": result.ambiguous_prompt_score,
        "overall_delivery_outcome_counts": result.overall_delivery_outcome_counts,
        "overall_failure_mode_counts": result.overall_failure_mode_counts,
        "overall_pass_hat_k": result.overall_pass_hat_k,
        "overall_ci_lower": result.overall_ci_lower,
        "overall_ci_upper": result.overall_ci_upper,
        "certified": result.certified,
        "environment_checksum": result.environment_checksum,
        "environment": str(result.environment),
        "tier_scores": {
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
        },
        "scenario_scores": {
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
        },
        "dimension_scores": {
            dimension: {
                item.value: {
                    "mean_task_score": item.mean_task_score,
                    "weighted_score": item.weighted_score,
                    "mean_completion": item.mean_completion,
                    "mean_trajectory": item.mean_trajectory,
                    "mean_behavior": item.mean_behavior,
                    "mean_judge": item.mean_judge,
                    "mean_reliability": item.mean_reliability,
                    "pass_hat_k_rate": item.pass_hat_k_rate,
                    "task_count": item.task_count,
                    "total_weight": item.total_weight,
                    "task_ids": item.task_ids,
                }
                for item in dimension_results
            }
            for dimension, dimension_results in result.dimension_results.items()
        },
        "task_results": [
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
                "category": task.category,
                "domain": task.domain,
                "functionality": task.functionality,
                "trace_distribution": task.trace_distribution,
                "tool_surface": task.tool_surface,
                "risk_tags": task.risk_tags,
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
                "mean_component_tokens": task.mean_component_tokens,
                "mean_total_tokens": task.mean_total_tokens,
                "mean_cost_usd": task.mean_cost_usd,
                "component_tokens_per_pass": task.component_tokens_per_pass,
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
        ],
    }

    api = HfApi(token=hf_token)
    ensure_dataset_repo(api, resolved_repo)

    # Read-then-append: load the existing submissions split, add the
    # new row, deduplicate by submission_id, push the combined dataset
    # so we never clobber prior rows.
    combined_rows: list[dict] = []
    try:
        from datasets import load_dataset

        existing = load_dataset(
            resolved_repo,
            split="submissions",
            token=hf_token,
        )
        combined_rows = [dict(r) for r in existing]
        logger.info(
            "Read %d existing submission row(s) from %s",
            len(combined_rows),
            resolved_repo,
        )
    except Exception as exc:
        logger.info(
            "No existing submissions split to append to (%s); starting fresh",
            exc,
        )

    new_submission_id = row.get("submission_id")
    if new_submission_id:
        combined_rows = [
            r for r in combined_rows
            if r.get("submission_id") != new_submission_id
        ]
    combined_rows.append(row)

    ds = Dataset.from_list(combined_rows)
    ds.push_to_hub(resolved_repo, split="submissions", token=hf_token)
    url = f"https://huggingface.co/datasets/{resolved_repo}"
    logger.info(
        "Results uploaded to %s (%d total submission rows)",
        url,
        len(combined_rows),
    )
    return url
