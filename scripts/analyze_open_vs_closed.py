#!/usr/bin/env python3
"""Open-source vs closed-source analyzer for the v0.5 historical DB.

Reads .clawbench/historical/profile_runs.json, splits profiles into
open-weights vs closed-source buckets by their base_model prefix, and
reports:

  - Per-bucket mean / worst-of-n / Taguchi S/N
  - Per-task win rates (which bucket wins each task)
  - Configuration-space diagnostic: does the open/closed axis explain
    variance better than the plugin-set axis? (via fANOVA importance)
  - Calibration error broken out by bucket

Usage:
    python scripts/analyze_open_vs_closed.py [--db <path>]
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from clawbench.factor_analysis import analyze
from clawbench.prediction import HistoricalDatabase
from clawbench.stats import compute_robustness_profile


CLOSED_PREFIXES = ("anthropic/", "openai/", "google/", "x-ai/", "xai/")
OPEN_PREFIXES = (
    "huggingface/", "hf/", "ollama/", "local/",
    "meta/", "meta-llama/",
)

# OpenRouter is a proxy — route by the inner vendor prefix.
OR_OPEN_INNER_PREFIXES = (
    "z-ai/", "zhipu/", "thudm/",      # GLM (Zhipu AI) — open weights
    "qwen/", "alibaba/",              # Qwen (Alibaba) — open weights
    "meta-llama/", "meta/",           # Llama
    "mistralai/", "mistral/",         # Mistral
    "deepseek-ai/", "deepseek/",      # DeepSeek — open weights
    "minimax/",                        # MiniMax — partially open
    "moonshotai/", "moonshot/",       # Kimi (Moonshot) — partially open
)
OR_CLOSED_INNER_PREFIXES = (
    "anthropic/", "openai/", "google/", "x-ai/", "xai/",
)


def classify(base_model: str) -> str:
    m = (base_model or "").lower()
    if m.startswith("openrouter/"):
        inner = m[len("openrouter/"):]
        if any(inner.startswith(p) for p in OR_OPEN_INNER_PREFIXES):
            return "open"
        if any(inner.startswith(p) for p in OR_CLOSED_INNER_PREFIXES):
            return "closed"
        return "unknown"
    if any(m.startswith(p) for p in CLOSED_PREFIXES):
        return "closed"
    if any(m.startswith(p) for p in OPEN_PREFIXES):
        return "open"
    return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        type=Path,
        default=REPO_ROOT / ".clawbench" / "historical" / "profile_runs.json",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"no historical database at {args.db}", file=sys.stderr)
        sys.exit(1)

    db = HistoricalDatabase(path=args.db)
    if not db.runs:
        print("historical database is empty")
        sys.exit(0)

    buckets: dict[str, list] = defaultdict(list)
    for run in db.runs:
        buckets[classify(run.fingerprint.base_model)].append(run)

    print(f"\nClawBench open-vs-closed split over {len(db)} historical runs\n")
    for bucket in ("closed", "open", "unknown"):
        runs = buckets.get(bucket, [])
        if not runs:
            continue
        scores = [r.overall_score for r in runs]
        print(f"  [{bucket:7}] n={len(runs):3}  mean={statistics.mean(scores):.3f}"
              f"  min={min(scores):.3f}  max={max(scores):.3f}")
        for r in runs:
            print(f"      · {r.profile_name:32}  {r.fingerprint.base_model:44}  {r.overall_score:.3f}")

    print()

    # Per-bucket Taguchi robustness profile over per-task averages
    print("Per-bucket robustness (Taguchi S/N over per-task means)")
    print("─" * 70)
    for bucket in ("closed", "open"):
        runs = buckets.get(bucket, [])
        if not runs:
            continue
        per_task_agg: dict[str, list[float]] = defaultdict(list)
        for r in runs:
            for task_id, score in r.per_task_score.items():
                per_task_agg[task_id].append(score)
        per_task_mean = {t: statistics.mean(scores) for t, scores in per_task_agg.items()}
        if not per_task_mean:
            print(f"  [{bucket}] no per-task scores recorded")
            continue
        rp = compute_robustness_profile(per_task_mean)
        print(
            f"  [{bucket:7}] tasks={rp.n_tasks:3}  mean={rp.mean:.3f}  "
            f"worst={rp.worst_of_n:.3f}  σ={rp.stddev:.3f}  "
            f"S/N={rp.sn_ratio_db:+.2f} dB"
        )
    print()

    # Per-task win rate
    print("Per-task win rate (open vs closed, mean score)")
    print("─" * 70)
    closed_task: dict[str, list[float]] = defaultdict(list)
    open_task: dict[str, list[float]] = defaultdict(list)
    for r in buckets.get("closed", []):
        for t, s in r.per_task_score.items():
            closed_task[t].append(s)
    for r in buckets.get("open", []):
        for t, s in r.per_task_score.items():
            open_task[t].append(s)
    tasks = sorted(set(closed_task.keys()) | set(open_task.keys()))
    closed_wins = open_wins = ties = 0
    for t in tasks:
        c = statistics.mean(closed_task[t]) if closed_task.get(t) else None
        o = statistics.mean(open_task[t]) if open_task.get(t) else None
        if c is None or o is None:
            continue
        if abs(c - o) < 0.02:
            ties += 1
            marker = "~"
        elif c > o:
            closed_wins += 1
            marker = "C"
        else:
            open_wins += 1
            marker = "O"
        print(f"  {marker}  {t:40}  closed {c:.3f}   open {o:.3f}   Δ {c - o:+.3f}")
    total = closed_wins + open_wins + ties
    if total:
        print(
            f"\n  Tally:  closed wins {closed_wins}/{total}   "
            f"open wins {open_wins}/{total}   ties {ties}/{total}"
        )
    print()

    # Calibration per bucket
    print("Calibration (prediction accuracy)")
    print("─" * 70)
    cal = db.calibration_metrics()
    print(f"  overall  n={cal['n']}  MAE={cal['mae']:.3f}  RMSE={cal['rmse']:.3f}  bias={cal['bias']:+.3f}")
    print()

    # fANOVA over the full database
    factor = analyze(db)
    print(f"Factor analysis: {factor.method} ({factor.n_runs} runs)")
    print("─" * 70)
    if not factor.main_effects:
        print("  (not enough distinct profiles — need ≥4)")
    else:
        for me in factor.main_effects[:10]:
            print(
                f"  {me.feature:40}  importance {me.importance:.3f}  "
                f"Δ {me.delta:+.3f}  (n_with={me.n_with}, n_without={me.n_without})"
            )
    print()


if __name__ == "__main__":
    main()
