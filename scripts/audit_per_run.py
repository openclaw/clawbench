"""Per-run 1-to-1 audit across every (model, task, run_idx) triple.

Flags issues beyond aggregate coverage:
  - Tasks where ALL models score 0 (task broken / verifier rejects everyone)
  - Tasks where models produce output but all get C=0 (verifier bug)
  - Tasks with suspiciously high cross-model infra-failure rates (harness bug)
  - Specific runs with harness errors (timeout, handshake)
  - Models with task-specific pathology (e.g., always fails on t3-X)
  - Judge failures per-task that haven't been rejudged
  - Missing runs in archive (logged but not cached)

Usage: python3 scripts/audit_per_run.py
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRIFT = ROOT / "data" / "drift_2026-04-19-full"
ARCH = ROOT / "data" / "run_cache_archive" / "v2026-4-19-full"

MODEL_MAP = {
    "opus46":   ("anthropic_claude-opus-4-6", "opus-4-6"),
    "opus47":   ("anthropic_claude-opus-4-7", "opus-4-7"),
    "sonnet46": ("anthropic_claude-sonnet-4-6", "sonnet-4-6"),
    "gpt54":    ("openai_gpt-5.4", "gpt-5.4"),
    "gemini":   ("google_gemini-3.1-pro-preview", "gemini-3.1-pro"),
    "glm":      ("openrouter_z-ai_glm-5.1", "glm-5.1"),
    "minimax":  ("openrouter_minimax_minimax-m2.7", "minimax-m2.7"),
    "kimi":     ("openrouter_moonshotai_kimi-k2.5", "kimi-k2.5"),
    "qwen":     ("openrouter_qwen_qwen3.6-plus", "qwen-3.6-plus"),
}

LOG_LINE = re.compile(
    r"^\[(\d+)/120\]\s+(\S+)\s+\([^)]+\)\s+run\s+(\d+):\s+([+\-~])\s+([\d.]+)"
)
HARNESS_ERR = re.compile(r"ERROR clawbench\.harness: Run (\S+)/(\d+) failed")
JUDGE_INFRA_PHRASES = [
    "gateway is restarting", "judge execution failed", "judge failed to run",
    "judge call failed", "judge timed out",
]


def parse_log(log_path: Path):
    runs = {}
    errors = {}
    if not log_path.exists():
        return runs, errors
    src = log_path.read_text(errors="ignore")
    for line in src.splitlines():
        m = LOG_LINE.match(line.strip())
        if m:
            seq, task, run_idx, outcome, score = m.groups()
            runs[(task, int(run_idx) - 1)] = {"score": float(score), "outcome": outcome}
        h = HARNESS_ERR.search(line)
        if h:
            errors[(h.group(1), int(h.group(2)))] = "harness_error"
    return runs, errors


def scan_archive(cache_dir: Path):
    out = {}
    if not cache_dir.exists():
        return out
    for tdir in cache_dir.iterdir():
        if not tdir.is_dir():
            continue
        for rf in tdir.glob("run*.json"):
            m = re.match(r"run(\d+)\.json", rf.name)
            if not m:
                continue
            try:
                d = json.load(open(rf))
            except Exception:
                continue
            jr = d.get("judge_result", {}) or {}
            reason = (jr.get("reason") or "").lower()
            # Don't flag rejudged runs as infra-failed even if reason is empty —
            # a rejudged run has a real judge call behind it (rejudged_at field).
            judge_infra = (
                jr.get("enabled")
                and "rejudged_at" not in jr
                and (
                    any(p in reason for p in JUDGE_INFRA_PHRASES)
                    or jr.get("error")
                    or (not reason.strip() and jr.get("score", 0) == 0)
                )
            )
            out[(tdir.name, int(m.group(1)))] = {
                "run_score": d.get("run_score", 0),
                "c": d.get("completion_result", {}).get("score", 0),
                "t": d.get("trajectory_result", {}).get("score", 0),
                "b": d.get("behavior_result", {}).get("score", 0),
                "j": jr.get("score", 0) if jr.get("enabled") else None,
                "judge_infra_failed": bool(judge_infra),
                "rejudged": "rejudged_at" in jr,
                "delivery": d.get("delivery_outcome"),
                "failure_mode": d.get("failure_mode"),
                "error": d.get("error"),
                "n_messages": len(d.get("transcript", {}).get("messages", [])),
                "has_assistant_text": any(
                    m.get("role") == "assistant" and m.get("text")
                    for m in d.get("transcript", {}).get("messages", [])
                ),
            }
    return out


def main():
    # Gather everything
    per_model = {}
    for label, (sub, pretty) in MODEL_MAP.items():
        log_p = DRIFT / f"docker_{label}_v2026-4-19-full.log"
        arch_d = ARCH / sub
        logged, errors = parse_log(log_p)
        archived = scan_archive(arch_d)
        per_model[pretty] = {
            "logged": logged, "errors": errors, "archived": archived,
        }

    # Build per-task cross-model view
    all_tasks = set()
    for m in per_model.values():
        for key in m["archived"]:
            all_tasks.add(key[0])
        for key in m["logged"]:
            all_tasks.add(key[0])

    # Issue classification
    issues = defaultdict(list)

    for task in sorted(all_tasks):
        # Collect all runs for this task across models
        task_runs_by_model = {}
        for pretty, data in per_model.items():
            task_runs = []
            for run_idx in range(3):
                key = (task, run_idx)
                a = data["archived"].get(key)
                logged = data["logged"].get(key)
                err = (key in data["errors"])
                task_runs.append({"archived": a, "logged": logged, "harness_err": err})
            task_runs_by_model[pretty] = task_runs

        # Compute cross-model stats
        all_scores = []
        all_cs = []
        all_outputs = []  # model produced assistant text?
        all_judge_infra = 0
        all_harness_err = 0
        for pretty, runs in task_runs_by_model.items():
            for r in runs:
                a = r["archived"]
                if a:
                    all_scores.append(a["run_score"])
                    all_cs.append(a["c"])
                    all_outputs.append(a["has_assistant_text"])
                    if a["judge_infra_failed"]:
                        all_judge_infra += 1
                elif r["logged"]:
                    all_scores.append(r["logged"]["score"])
                if r["harness_err"]:
                    all_harness_err += 1

        if not all_scores:
            continue
        mean_score = sum(all_scores) / len(all_scores)
        mean_c = sum(all_cs) / len(all_cs) if all_cs else 0
        output_rate = sum(all_outputs) / len(all_outputs) if all_outputs else 0

        # Flag issues
        if mean_score < 0.1:
            issues["task_fails_all_models"].append((task, mean_score, output_rate))
        if mean_c < 0.05 and output_rate > 0.5:
            issues["verifier_rejects_valid_outputs"].append((task, mean_c, output_rate))
        if all_harness_err >= 5:
            issues["harness_errors_cluster"].append((task, all_harness_err))
        if all_judge_infra >= 5:
            issues["judge_infra_cluster"].append((task, all_judge_infra))

    # Print issues
    print("=" * 70)
    print("ISSUE: Tasks where ALL models score near-zero (broken verifier or task)")
    print("=" * 70)
    for task, mean, out_rate in sorted(issues["task_fails_all_models"]):
        print(f"  {task:<40}  mean_score={mean:.3f}  assistant_output_rate={out_rate:.1%}")

    print()
    print("=" * 70)
    print("ISSUE: Verifier rejects valid outputs (model produced text but C=0)")
    print("=" * 70)
    for task, mean_c, out_rate in sorted(issues["verifier_rejects_valid_outputs"]):
        print(f"  {task:<40}  mean_completion={mean_c:.3f}  assistant_output_rate={out_rate:.1%}")

    print()
    print("=" * 70)
    print("ISSUE: Harness-error clusters (gateway failures per task)")
    print("=" * 70)
    for task, n in sorted(issues["harness_errors_cluster"], key=lambda x: -x[1]):
        print(f"  {task:<40}  harness_error_count={n}")

    print()
    print("=" * 70)
    print("ISSUE: Judge-infra clusters (judge failing per task)")
    print("=" * 70)
    for task, n in sorted(issues["judge_infra_cluster"], key=lambda x: -x[1]):
        print(f"  {task:<40}  judge_infra_failures={n}  (should be rejudged)")

    # Per-model per-task pathologies
    print()
    print("=" * 70)
    print("ISSUE: Model-specific task pathologies (all 3 runs of a task scored 0 on one model)")
    print("=" * 70)
    for pretty, data in per_model.items():
        zero_tasks = []
        for task in sorted(all_tasks):
            all_three_zero = True
            any_attempted = False
            for run_idx in range(3):
                key = (task, run_idx)
                a = data["archived"].get(key)
                logged = data["logged"].get(key)
                if a:
                    any_attempted = True
                    if a["run_score"] > 0.01:
                        all_three_zero = False
                elif logged:
                    any_attempted = True
                    if logged["score"] > 0.01:
                        all_three_zero = False
                else:
                    all_three_zero = False  # can't confirm
                    any_attempted = False
            if any_attempted and all_three_zero:
                zero_tasks.append(task)
        if zero_tasks:
            print(f"  {pretty:<18}: all-zero on {len(zero_tasks)} tasks")
            for t in zero_tasks[:6]:
                print(f"    - {t}")

    # Task coverage mismatches
    print()
    print("=" * 70)
    print("COVERAGE: Models with non-complete coverage (logged != 120 or archived != 120)")
    print("=" * 70)
    for pretty, data in per_model.items():
        n_log = len(data["logged"])
        n_arch = len(data["archived"])
        if n_log < 120 or n_arch < 120:
            print(f"  {pretty:<18}  logged={n_log:<4}  archived={n_arch:<4}  missing={120 - max(n_log, n_arch)}")


if __name__ == "__main__":
    main()
