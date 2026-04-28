"""Ingest a real ClawBench v0.4 result JSON into the v0.5 framework.

Usage:
    python scripts/ingest_real_run.py <result.json> --profile-name <name>

This bridges the v0.4 deterministic results into the v0.5 configuration-space
analysis. It builds a Plugin Profile from the model + the bundled openclaw
plugin set, computes the fingerprint, and adds the run to the historical DB.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clawbench.diagnostic import build_diagnostic, submit_run
from clawbench.prediction import HistoricalDatabase
from clawbench.profile import (
    PluginManifest,
    PluginProfile,
    PluginProfileEntry,
)


def extract_per_task_scores(data: dict) -> dict[str, float]:
    """Pull per-task scores out of the v0.4 results JSON."""
    scores: dict[str, float] = {}
    for tier in data.get("tier_results", []):
        for task in tier.get("task_stats", []):
            tid = task.get("task_id")
            mean = task.get("mean_task_score") or task.get("mean_run_score") or 0.0
            if tid:
                scores[tid] = float(mean)
    return scores


def build_profile_from_results(data: dict, profile_name: str) -> PluginProfile:
    model = data.get("model", "unknown")
    return PluginProfile(
        name=profile_name,
        base_model=model,
        plugins=[
            PluginProfileEntry(id="anthropic"),
            PluginProfileEntry(id="memory-lancedb"),
            PluginProfileEntry(id="browser-playwright"),
        ],
        slots={"memory": "memory-lancedb"},
        tools_allow=["bash", "file_read", "file_edit", "memory_read", "memory_write"],
        notes=f"Real benchmark run on {data.get('task_count', '?')} tasks, "
              f"submission {data.get('submission_id', '')}",
    )


# Minimal manifests so the framework can fingerprint the profile
MANIFESTS: dict[str, PluginManifest] = {
    "anthropic": PluginManifest(
        id="anthropic",
        providers=["anthropic"],
        capability_tags=["llm-provider"],
        clawhub_is_official=True,
    ),
    "memory-lancedb": PluginManifest(
        id="memory-lancedb",
        kind=["memory"],
        contracts={
            "memoryEmbeddingProviders": ["lancedb"],
            "tools": ["memory_write", "memory_read"],
        },
        capability_tags=["memory", "vector-search"],
        clawhub_is_official=True,
    ),
    "browser-playwright": PluginManifest(
        id="browser-playwright",
        contracts={"tools": ["browser_navigate", "browser_click", "browser_extract"]},
        capability_tags=["browser", "scraping"],
        clawhub_is_official=True,
    ),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("result_json", type=Path)
    parser.add_argument("--profile-name", required=True)
    parser.add_argument(
        "--db", type=Path,
        default=Path(__file__).resolve().parents[1] / ".clawbench/historical/profile_runs.json",
    )
    parser.add_argument("--no-record", action="store_true")
    args = parser.parse_args()

    with args.result_json.open() as f:
        data = json.load(f)

    overall = float(data.get("overall_score", 0.0))
    per_task = extract_per_task_scores(data)
    profile = build_profile_from_results(data, args.profile_name)

    print(f"Loaded {args.result_json}")
    print(f"  model:        {data.get('model')}")
    print(f"  overall:      {overall:.4f}")
    print(f"  per-task:     {len(per_task)} tasks")
    for tid, s in per_task.items():
        print(f"    {tid:30} {s:.4f}")
    print(f"  cost/pass:    ${data.get('overall_cost_per_pass', 0):.4f}")
    print(f"  tokens/pass:  {data.get('overall_tokens_per_pass', 0):,.0f}")
    print()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    db = HistoricalDatabase(path=args.db)
    print(f"Historical DB has {len(db)} runs before this one.")

    if args.no_record:
        report = build_diagnostic(
            profile=profile,
            manifests=MANIFESTS,
            db=db,
            actual_overall_score=overall,
            actual_per_task_scores=per_task,
        )
    else:
        report = submit_run(
            profile=profile,
            manifests=MANIFESTS,
            db=db,
            actual_overall_score=overall,
            actual_per_task_scores=per_task,
        )

    print(report.render_text())


if __name__ == "__main__":
    main()
