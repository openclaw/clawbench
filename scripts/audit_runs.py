"""Comprehensive per-run audit across all models in drift_2026-04-19-full.

For each model, cross-references:
  1. Log file (docker_<label>_<tag>.log) — all [N/120] run attempts + their scores
  2. Archived per-run JSONs (run_cache_archive/<tag>/<cache_sub>/<task>/runN.json)
  3. Judge status per cached run (rejudged via direct API or not)

Outputs a fair-comparison table: coverage %, infra-failure %, clean mean,
coverage-normalized score, judge coverage.

Usage:
  python3 scripts/audit_runs.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRIFT = ROOT / "data" / "drift_2026-04-19-full"
ARCH = ROOT / "data" / "run_cache_archive" / "v2026-4-19-full"

# Model label (in log filenames) → (cache_sub, pretty name)
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

# Regex to parse "[N/120] task (tier/family) run R: + 0.93 C=1.00 T=0.90 ..."
LOG_LINE = re.compile(
    r"^\[(\d+)/120\]\s+(\S+)\s+\([^)]+\)\s+run\s+(\d+):\s+([+\-~])\s+([\d.]+)"
)
JUDGE_INFRA_PHRASES = [
    "gateway is restarting",
    "judge execution failed",
    "judge failed to run",
    "judge call failed",
    "judge timed out",
]


def parse_log(path: Path) -> dict:
    """Return: {(task_id, run_idx): {"score": float, "outcome": "+/-/~"}} from log file."""
    runs = {}
    if not path.exists():
        return runs
    for line in path.read_text(errors="ignore").splitlines():
        m = LOG_LINE.match(line.strip())
        if not m:
            continue
        seq, task, run_idx, outcome, score = m.groups()
        # Log uses 1-indexed run numbers; archive uses 0-indexed runN.json.
        # Normalize to 0-indexed so keys cross-reference correctly.
        key = (task, int(run_idx) - 1)
        # Later entries overwrite earlier (retry semantics)
        runs[key] = {"score": float(score), "outcome": outcome, "seq": int(seq)}
    return runs


def scan_archive(cache_dir: Path) -> dict:
    """Return: {(task_id, run_idx): {"run_score": float, "c": float, "judge_err": bool, "rejudged": bool}}"""
    out = {}
    if not cache_dir.exists():
        return out
    for tdir in cache_dir.iterdir():
        if not tdir.is_dir():
            continue
        for rf in tdir.glob("run*.json"):
            try:
                d = json.load(open(rf))
            except Exception:
                continue
            m_run = re.match(r"run(\d+)\.json", rf.name)
            if not m_run:
                continue
            run_idx = int(m_run.group(1))
            jr = d.get("judge_result", {}) or {}
            reason = (jr.get("reason") or "").lower()
            judge_infra = (
                any(p in reason for p in JUDGE_INFRA_PHRASES)
                or jr.get("error")
                or (not reason.strip() and jr.get("score", 0) == 0)
            )
            out[(tdir.name, run_idx)] = {
                "run_score": d.get("run_score", 0),
                "completion": d.get("completion_result", {}).get("score", 0),
                "judge_score": jr.get("score", 0) if jr.get("enabled") else None,
                "judge_infra_failed": bool(judge_infra and jr.get("enabled")),
                "rejudged": "rejudged_at" in jr,
                "delivery": d.get("delivery_outcome"),
                "failure_mode": d.get("failure_mode"),
            }
    return out


def audit_model(label: str, cache_sub: str, pretty: str) -> dict:
    log_path = DRIFT / f"docker_{label}_v2026-4-19-full.log"
    cache_dir = ARCH / cache_sub
    logged = parse_log(log_path)
    archived = scan_archive(cache_dir)

    n_log = len(logged)
    n_arch = len(archived)
    not_archived = [k for k in logged.keys() if k not in archived]
    # Classify runs
    clean_runs = []                 # logged + archived + not-infra-zero + judge-OK
    infra_zero_runs = []            # logged 0.00 (infra) — never landed in archive
    archived_zero = []              # archived but run_score = 0 (infra/capability)
    judge_infra = []                # archived with judge_infra_failed
    rejudged = []                   # archived with rejudged_at

    for k, a in archived.items():
        if a["judge_infra_failed"] and not a["rejudged"]:
            judge_infra.append(k)
        if a["rejudged"]:
            rejudged.append(k)
        if a["run_score"] < 0.01:
            archived_zero.append(k)
        else:
            clean_runs.append((k, a["run_score"]))

    # Runs that got logged at 0.00 but weren't archived are pure infra-failures
    for k in not_archived:
        if logged[k]["score"] < 0.01:
            infra_zero_runs.append(k)
        else:
            clean_runs.append((k, logged[k]["score"]))

    # Score computations
    all_scores = []
    for k, a in archived.items():
        all_scores.append(a["run_score"])
    for k in not_archived:
        all_scores.append(logged[k]["score"])

    expected = 120

    clean_scores = [s for _, s in clean_runs]
    clean_mean = sum(clean_scores) / len(clean_scores) if clean_scores else 0

    all_mean = sum(all_scores) / len(all_scores) if all_scores else 0
    # Coverage-normalized: clean_mean with gap-penalty (missing runs count as 0)
    coverage_normalized = (sum(clean_scores) + 0 * max(0, expected - len(clean_scores))) / expected

    return {
        "label": label,
        "pretty": pretty,
        "n_log_entries": n_log,
        "n_archived": n_arch,
        "n_missing_from_archive": len(not_archived),
        "n_clean_runs": len(clean_runs),
        "n_archived_zero": len(archived_zero),
        "n_logged_infra_zero": len(infra_zero_runs),
        "n_judge_infra_failed": len(judge_infra),
        "n_rejudged": len(rejudged),
        "coverage_pct": 100.0 * len(clean_runs) / expected,
        "clean_mean": clean_mean,
        "all_mean": all_mean,
        "coverage_normalized": coverage_normalized,
    }


def main():
    print(f"{'Model':<16} {'Logged':>7} {'Archv':>6} {'Clean':>6} {'Cov%':>5}  {'all_mean':>8} {'clean':>7} {'cov_norm':>8} {'infra_0':>8} {'j_rejdg':>8} {'j_failed':>8}")
    print(f"{'-'*16} {'-'*7} {'-'*6} {'-'*6} {'-'*5}  {'-'*8} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    rows = []
    for label, (cache_sub, pretty) in MODEL_MAP.items():
        r = audit_model(label, cache_sub, pretty)
        rows.append(r)

    # Sort by coverage-normalized score
    rows.sort(key=lambda r: -r["coverage_normalized"])
    for r in rows:
        print(
            f"  {r['pretty']:<14} {r['n_log_entries']:>7} {r['n_archived']:>6} "
            f"{r['n_clean_runs']:>6} {r['coverage_pct']:>4.0f}%  "
            f"{r['all_mean']:>8.4f} {r['clean_mean']:>7.4f} "
            f"{r['coverage_normalized']:>8.4f} "
            f"{r['n_logged_infra_zero']+r['n_archived_zero']:>8} "
            f"{r['n_rejudged']:>8} {r['n_judge_infra_failed']:>8}"
        )

    # Show gaps explicitly
    print()
    print("Legend:")
    print("  all_mean      = mean of ALL attempts (log+archive merged; infra-zeros pull this DOWN)")
    print("  clean         = mean excluding infra-failed runs (shows capability ceiling)")
    print("  cov_norm      = clean*coverage + 0*missing; all models scored against 120-run denominator")
    print("  infra_0       = runs that scored 0 due to infrastructure (gateway/state/handshake failures)")
    print("  j_rejdg       = judge scores that have been rejudged via direct Anthropic API")
    print("  j_failed      = judge infra-failures that have NOT been rejudged")


if __name__ == "__main__":
    main()
