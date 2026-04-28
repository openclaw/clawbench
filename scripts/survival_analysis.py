#!/usr/bin/env python3
"""Per-turn survival analysis on posterior cached runs.

For each run, define a failure time T_F as the first assistant turn where the
agent emits neither text nor tool calls, or the final assistant turn of an
unsuccessful run with delivery outcome in {fail, partial}.

We then estimate:

    S(t) = P(T_F > t)
    h(t) = P(T_F = t | T_F >= t)

This exposes long-horizon fragility that is easy to hide in flat mean scores.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawbench.dynamics_archive import load_task_runs_by_model

SUCCESS_THRESHOLD = 0.7


def assistant_turns(run) -> list:
    return run.transcript.assistant_messages


def find_failure_turn(run) -> tuple[int, bool]:
    """Return (failure_turn, is_event) with 1-indexed assistant turns."""
    turns = assistant_turns(run)
    n = len(turns)

    for idx, turn in enumerate(turns, 1):
        has_text = bool((turn.text or "").strip())
        has_tool_call = bool(turn.tool_calls)
        if not has_text and not has_tool_call:
            return idx, True

    if run.run_score < SUCCESS_THRESHOLD and run.delivery_outcome.value in {"fail", "partial"}:
        return max(n, 1), True

    return max(n, 1), False


def empirical_survival(times_events: list[tuple[int, bool]], max_t: int = 20) -> list[float]:
    """Empirical survival curve S(t) over assistant-turn index."""
    total = len(times_events)
    if total == 0:
        return [0.0] * max_t

    survival = []
    for t in range(1, max_t + 1):
        survived = sum(
            1
            for tf, is_event in times_events
            if (not is_event and tf >= t) or (is_event and tf > t)
        )
        survival.append(survived / total)
    return survival


def hazard(times_events: list[tuple[int, bool]], max_t: int = 20) -> list[float]:
    """Discrete hazard h(t) = events_at_t / at_risk_at_t."""
    hazard_vals = []
    for t in range(1, max_t + 1):
        at_risk = sum(1 for tf, _ in times_events if tf >= t)
        events_at_t = sum(1 for tf, is_event in times_events if is_event and tf == t)
        hazard_vals.append(events_at_t / at_risk if at_risk > 0 else 0.0)
    return hazard_vals


def main() -> None:
    parser = argparse.ArgumentParser(description="Survival analysis on cached runs")
    parser.add_argument("--archive-dir", type=Path, default=Path(".clawbench/run_cache"))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--tier", choices=["tier1", "tier2", "tier3", "tier4", "tier5"], default=None)
    parser.add_argument("--max-turn", type=int, default=20)
    args = parser.parse_args()

    grouped = load_task_runs_by_model(args.archive_dir, tier=args.tier)
    if not grouped:
        raise SystemExit(f"No cached runs found under {args.archive_dir}")

    out = {}
    for model_name, task_runs in grouped.items():
        events = []
        for runs in task_runs.values():
            for run in runs:
                events.append(find_failure_turn(run))

        n_runs = len(events)
        n_events = sum(1 for _, is_event in events if is_event)
        event_times = [t for t, is_event in events if is_event]
        med = median(event_times) if event_times else float("inf")

        out[model_name] = {
            "pretty": model_name,
            "n_runs": n_runs,
            "n_events": n_events,
            "median_fail_turn": med,
            "survival": empirical_survival(events, max_t=args.max_turn),
            "hazard": hazard(events, max_t=args.max_turn),
        }

    args.reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.reports_dir / "survival_analysis.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
