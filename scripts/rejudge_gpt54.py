"""Re-judge GPT 5.4 runs that had judge auth errors.

Calls Sonnet 4.6 directly via the Anthropic API (no gateway needed).
Updates cached run files and regenerates the benchmark result.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

import anthropic
import yaml

# ── paths ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "run_cache" / "openai_gpt-5.4"
RESULT_PATH = ROOT / "data" / "results" / "8b3f748b-47e6-43a6-b62e-2a79c6e1c5e4.json"
TASK_DIRS = [ROOT / "tasks" / f"tier{i}" for i in range(1, 6)]

# ── API key ──────────────────────────────────────────────────────────────
def get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    cfg_path = Path.home() / ".openclaw" / "openclaw.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text())
        key = cfg.get("env", {}).get("ANTHROPIC_API_KEY")
        if key:
            return key
    raise RuntimeError("No ANTHROPIC_API_KEY found")

# ── load task definitions ────────────────────────────────────────────────
def load_tasks() -> dict[str, dict]:
    tasks = {}
    for task_dir in TASK_DIRS:
        if not task_dir.exists():
            continue
        for yaml_file in sorted(task_dir.glob("*.yaml")):
            task = yaml.safe_load(yaml_file.read_text())
            tasks[task["id"]] = task
    return tasks

# ── build judge prompt (mirrors clawbench/judge.py) ──────────────────────
def build_judge_prompt(task: dict, run: dict) -> str:
    judge = task.get("judge", {})
    rubric = judge.get("rubric", "").strip()
    threshold = judge.get("passing_threshold", 0.7)
    cr = run.get("completion_result", {})

    sections = [
        "You are evaluating one ClawBench agent run.",
        "Score only the task-specific quality rubric below.",
        'Return JSON only with keys "score", "confidence", "reason", "rubric_hits", and "rubric_misses".',
        "Do not use tools. Do not add markdown.",
        "",
        f"Task ID: {task['id']}",
        f"Task name: {task['name']}",
        f"Judge threshold: {threshold:.2f}",
        "Rubric:",
        rubric,
    ]

    if judge.get("include_completion_feedback", True):
        passed = cr.get("passed_assertions", 0)
        total = cr.get("total_assertions", 0)
        score = cr.get("score", 0)
        sections.extend([
            "",
            "Deterministic verifier summary:",
            f"- completion assertions: {passed}/{total}",
            f"- completion score: {score:.3f}",
        ])
        failed = cr.get("failed_assertions", [])
        if failed:
            sections.append("- failures:")
            for f in failed[:6]:
                sections.append(f"  - {f}")

    if judge.get("include_transcript", True):
        transcript = run.get("transcript", {})
        excerpt = render_transcript_excerpt(transcript)
        if excerpt:
            sections.extend(["", "Transcript excerpt:", excerpt])

    sections.extend([
        "",
        "Scoring guidance:",
        "- 1.0 means the output is fully correct, grounded, and high quality for this rubric.",
        "- 0.7 means acceptable and usable.",
        "- 0.4 means partial or shaky.",
        "- 0.0 means missing, wrong, unsafe, or hallucinated.",
    ])
    return "\n".join(sections).strip()


def render_transcript_excerpt(transcript: dict, max_chars: int = 4000) -> str:
    messages = transcript.get("messages", [])
    tool_calls = transcript.get("tool_call_sequence", [])

    family_counts = Counter(tc.get("family") or tc.get("name", "unknown") for tc in tool_calls)
    failed_calls = [
        f"{tc.get('family') or tc.get('name')}: {tc.get('error') or tc.get('output', '')}"
        for tc in tool_calls
        if tc.get("success") is False and (tc.get("error") or tc.get("output"))
    ]

    header_lines = []
    if family_counts:
        header_lines.append(
            "tool families: " + ", ".join(f"{f} x{c}" for f, c in sorted(family_counts.items()))
        )
    if failed_calls:
        header_lines.append("tool failures:")
        for item in failed_calls[:5]:
            header_lines.append(f"  - {item[:180]}")

    message_lines = []
    for msg in messages[-10:]:
        text = (msg.get("text") or "").strip()
        role = msg.get("role", "unknown").upper()
        if text:
            message_lines.append(f"[{role}] {text[:500]}")
        for tc in msg.get("tool_calls", [])[:4]:
            state = "ok" if tc.get("success") is not False else "failed"
            message_lines.append(f"[{role} TOOL] {tc.get('family') or tc.get('name')} ({state})")

    combined = "\n".join([*header_lines, *message_lines]).strip()
    return combined[:max_chars]


# ── parse judge response (mirrors clawbench/judge.py) ────────────────────
def parse_judge_response(raw_text: str, passing_threshold: float) -> dict:
    payload = extract_json_payload(raw_text)
    if payload is None:
        payload = extract_labeled_payload(raw_text)
    if payload is None:
        return {
            "enabled": True,
            "error": "Judge response did not contain valid JSON.",
            "reason": raw_text[:600],
            "score": 0.0,
            "confidence": 0.0,
            "passed": False,
            "rubric_hits": [],
            "rubric_misses": [],
        }

    score = max(0.0, min(1.0, float(payload.get("score", 0.0))))
    confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.0))))
    return {
        "enabled": True,
        "score": score,
        "confidence": confidence,
        "passed": score >= passing_threshold,
        "reason": str(payload.get("reason", ""))[:600],
        "rubric_hits": [str(x)[:200] for x in (payload.get("rubric_hits") or []) if str(x).strip()],
        "rubric_misses": [str(x)[:200] for x in (payload.get("rubric_misses") or []) if str(x).strip()],
        "error": None,
    }


def extract_json_payload(raw_text: str) -> dict | None:
    candidate = raw_text.strip()
    if not candidate:
        return None
    for attempt in [candidate, strip_code_fences(candidate), slice_json(candidate)]:
        if not attempt:
            continue
        try:
            parsed = json.loads(attempt)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def strip_code_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```") and s.endswith("```"):
        lines = s.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return s


def slice_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ""
    return text[start:end + 1]


def extract_labeled_payload(raw_text: str) -> dict | None:
    score_match = re.search(r'(?im)^\s*"?score"?\s*[:=]\s*([0-9]*\.?[0-9]+)', raw_text)
    conf_match = re.search(r'(?im)^\s*"?confidence"?\s*[:=]\s*([0-9]*\.?[0-9]+)', raw_text)
    if score_match is None and conf_match is None:
        return None
    return {
        "score": float(score_match.group(1)) if score_match else 0.0,
        "confidence": float(conf_match.group(1)) if conf_match else 0.0,
        "reason": "",
        "rubric_hits": [],
        "rubric_misses": [],
    }


# ── scoring (mirrors clawbench/scorer.py) ────────────────────────────────
DETERMINISTIC_FLOOR = 0.9999

def combine_run_score(completion: float, trajectory: float, behavior: float,
                      judge: float | None, has_det_verifier: bool) -> float:
    if judge is None:
        w_sum = 0.40 * completion + 0.30 * trajectory + 0.20 * behavior
        total = 0.90
    elif has_det_verifier:
        if completion < DETERMINISTIC_FLOOR:
            w_sum = 0.40 * completion + 0.30 * trajectory + 0.20 * behavior
            total = 0.90
        else:
            w_sum = 0.40 * completion + 0.30 * trajectory + 0.20 * behavior + 0.10 * judge
            total = 1.00
    else:
        w_sum = 0.20 * completion + 0.20 * trajectory + 0.10 * behavior + 0.50 * judge
        total = 1.00
    return round(min(1.0, max(0.0, w_sum / total)), 4)


# ── main ─────────────────────────────────────────────────────────────────
async def main():
    api_key = get_api_key()
    client = anthropic.Anthropic(api_key=api_key)
    tasks = load_tasks()
    print(f"Loaded {len(tasks)} task definitions")

    # Find affected runs
    affected = []
    for task_dir in sorted(CACHE_DIR.iterdir()):
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        for run_file in sorted(task_dir.glob("run*.json")):
            run = json.loads(run_file.read_text())
            jr = run.get("judge_result", {})
            if jr.get("error"):
                affected.append((task_id, run_file, run))

    print(f"Found {len(affected)} runs with judge errors")
    if not affected:
        print("Nothing to re-judge!")
        return

    # Re-judge each run
    succeeded = 0
    failed = 0
    for i, (task_id, run_path, run) in enumerate(affected):
        task = tasks.get(task_id)
        if not task or not task.get("judge"):
            print(f"  [{i+1}/{len(affected)}] {task_id}/{run_path.name}: no judge config, skipping")
            continue

        prompt = build_judge_prompt(task, run)
        threshold = task["judge"].get("passing_threshold", 0.7)

        print(f"  [{i+1}/{len(affected)}] {task_id}/{run_path.name}: judging...", end=" ", flush=True)
        started = time.monotonic()
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.content[0].text
            duration_ms = int((time.monotonic() - started) * 1000)

            parsed = parse_judge_response(raw_text, threshold)
            parsed["model"] = "anthropic/claude-sonnet-4-6"
            parsed["duration_ms"] = duration_ms
            parsed["token_usage"] = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }

            # Update the run's judge_result
            run["judge_result"] = parsed

            # Recompute run_score
            cr = run.get("completion_result", {})
            tr = run.get("trajectory_result", {})
            br = run.get("behavior_result", {})
            has_det = cr.get("total_assertions", 0) > 0
            j_score = parsed["score"] if parsed["enabled"] and not parsed.get("error") else None
            new_run_score = combine_run_score(
                cr.get("score", 0), tr.get("score", 0), br.get("score", 0),
                j_score, has_det
            )
            old_score = run.get("run_score", 0)
            run["run_score"] = new_run_score

            # Save updated run
            tmp = run_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(run, indent=2))
            tmp.replace(run_path)

            delta = new_run_score - old_score
            delta_str = f" (delta={delta:+.4f})" if abs(delta) > 0.0001 else ""
            print(f"J={parsed['score']:.3f} conf={parsed['confidence']:.3f}{delta_str} ({duration_ms}ms)")
            succeeded += 1

        except Exception as exc:
            failed += 1
            print(f"ERROR: {exc}")
            continue

    print(f"\nRe-judging complete: {succeeded} succeeded, {failed} failed")

    # ── Re-aggregate benchmark result ────────────────────────────────────
    print("\nRe-aggregating benchmark result...")
    result = json.loads(RESULT_PATH.read_text())

    # Reload ALL runs and recompute task-level and overall metrics
    all_task_runs: dict[str, list[dict]] = {}
    for task_dir in sorted(CACHE_DIR.iterdir()):
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        runs = []
        for run_file in sorted(task_dir.glob("run*.json")):
            runs.append(json.loads(run_file.read_text()))
        if runs:
            all_task_runs[task_id] = runs

    # Update each task_result in the benchmark result
    import numpy as np

    all_scores = []
    all_completion = []
    all_trajectory = []
    all_behavior = []
    all_judge = []
    all_judge_conf = []
    total_judge_errors = 0
    total_judged_tasks = 0
    all_judge_pass_rates = []
    all_reliability = []

    for task_result in result.get("task_results", []):
        task_id = task_result["task_id"]
        runs = all_task_runs.get(task_id, [])
        if not runs:
            continue

        run_scores = [r["run_score"] for r in runs]
        c_scores = [r["completion_result"]["score"] for r in runs]
        t_scores = [r["trajectory_result"]["score"] for r in runs]
        b_scores = [r["behavior_result"]["score"] for r in runs]

        # Judge metrics
        judged = [r for r in runs if r["judge_result"].get("enabled") and not r["judge_result"].get("error")]
        j_errors = sum(1 for r in runs if r["judge_result"].get("error"))
        j_scores = [r["judge_result"]["score"] for r in judged]
        j_confs = [r["judge_result"]["confidence"] for r in judged]
        j_passed = [r for r in judged if r["judge_result"].get("passed")]

        task_result["scores"] = run_scores
        task_result["mean_run_score"] = round(sum(run_scores) / len(run_scores), 4)
        task_result["mean_completion_score"] = round(sum(c_scores) / len(c_scores), 4)
        task_result["mean_trajectory_score"] = round(sum(t_scores) / len(t_scores), 4)
        task_result["mean_behavior_score"] = round(sum(b_scores) / len(b_scores), 4)
        task_result["judged_runs"] = len(judged)
        task_result["judge_error_count"] = j_errors
        task_result["mean_judge_score"] = round(sum(j_scores) / len(j_scores), 4) if j_scores else 0.0
        task_result["mean_judge_confidence"] = round(sum(j_confs) / len(j_confs), 4) if j_confs else 0.0
        task_result["judge_pass_rate"] = round(len(j_passed) / len(judged), 4) if judged else 0.0

        # Reliability
        pass_threshold = 0.7
        passed_runs = [s for s in run_scores if s >= pass_threshold]
        pass_rate = len(passed_runs) / len(run_scores)
        pass_hat_k = 1.0 if len(passed_runs) == len(run_scores) else 0.0
        variance = sum((s - sum(run_scores)/len(run_scores))**2 for s in run_scores) / len(run_scores)
        stddev = variance ** 0.5
        variance_score = max(0.0, min(1.0, 1.0 - (stddev / 0.2)))
        reliability = 0.5 * pass_hat_k + 0.3 * pass_rate + 0.2 * variance_score
        task_result["reliability_score"] = round(reliability, 4)
        task_result["variance_score"] = round(variance_score, 4)
        task_result["stddev"] = round(stddev, 4)
        task_result["min_score"] = round(min(run_scores), 4)
        task_result["max_score"] = round(max(run_scores), 4)

        # Bootstrap CI
        arr = np.array(run_scores)
        boot_means = [np.mean(np.random.choice(arr, size=len(arr), replace=True)) for _ in range(10000)]
        ci_lo = round(float(np.percentile(boot_means, 2.5)), 4)
        ci_hi = round(float(np.percentile(boot_means, 97.5)), 4)
        task_result["ci_lower"] = ci_lo
        task_result["ci_upper"] = ci_hi

        # Task score
        mean_score = sum(run_scores) / len(run_scores)
        task_score = round(0.9 * mean_score + 0.1 * reliability, 4)
        task_result["mean_task_score"] = task_score

        all_scores.append(task_score)
        all_completion.append(task_result["mean_completion_score"])
        all_trajectory.append(task_result["mean_trajectory_score"])
        all_behavior.append(task_result["mean_behavior_score"])
        if j_scores:
            all_judge.append(task_result["mean_judge_score"])
            all_judge_conf.append(task_result["mean_judge_confidence"])
            all_judge_pass_rates.append(task_result["judge_pass_rate"])
            total_judged_tasks += 1
        total_judge_errors += j_errors
        all_reliability.append(reliability)

    # Update overall metrics
    result["overall_score"] = round(sum(all_scores) / len(all_scores), 4) if all_scores else 0
    result["overall_completion"] = round(sum(all_completion) / len(all_completion), 4) if all_completion else 0
    result["overall_trajectory"] = round(sum(all_trajectory) / len(all_trajectory), 4) if all_trajectory else 0
    result["overall_behavior"] = round(sum(all_behavior) / len(all_behavior), 4) if all_behavior else 0
    result["overall_judge_score"] = round(sum(all_judge) / len(all_judge), 4) if all_judge else 0
    result["overall_judge_confidence"] = round(sum(all_judge_conf) / len(all_judge_conf), 4) if all_judge_conf else 0
    result["overall_judge_pass_rate"] = round(sum(all_judge_pass_rates) / len(all_judge_pass_rates), 4) if all_judge_pass_rates else 0
    result["judge_task_coverage"] = round(total_judged_tasks / len(all_scores), 4) if all_scores else 0
    result["judge_error_count"] = total_judge_errors
    result["overall_reliability"] = round(sum(all_reliability) / len(all_reliability), 4) if all_reliability else 0

    # Bootstrap CI for overall
    arr = np.array(all_scores)
    boot = [float(np.mean(np.random.choice(arr, size=len(arr), replace=True))) for _ in range(10000)]
    result["ci_lower"] = round(float(np.percentile(boot, 2.5)), 4)
    result["ci_upper"] = round(float(np.percentile(boot, 97.5)), 4)

    # Update tier results
    tier_map: dict[str, list] = {}
    for tr in result.get("task_results", []):
        tier = tr.get("tier", "")
        tier_map.setdefault(tier, []).append(tr)
    for tier_result in result.get("tier_results", []):
        tier = tier_result.get("tier", "")
        tasks_in_tier = tier_map.get(tier, [])
        if tasks_in_tier:
            scores = [t["mean_task_score"] for t in tasks_in_tier]
            tier_result["mean_task_score"] = round(sum(scores) / len(scores), 4)
            j_scores_t = [t["mean_judge_score"] for t in tasks_in_tier if t["judged_runs"] > 0]
            if j_scores_t:
                tier_result["mean_judge_score"] = round(sum(j_scores_t) / len(j_scores_t), 4)

    # Save updated result
    tmp = RESULT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, indent=2))
    tmp.replace(RESULT_PATH)

    print(f"\nUpdated result file: {RESULT_PATH}")
    print(f"  overall_score: {result['overall_score']}")
    print(f"  overall_judge_score: {result['overall_judge_score']}")
    print(f"  judge_task_coverage: {result['judge_task_coverage']}")
    print(f"  judge_error_count: {result['judge_error_count']}")
    print(f"  overall_reliability: {result['overall_reliability']}")
    print(f"  95% CI: [{result['ci_lower']}, {result['ci_upper']}]")

    # Print updated per-task summary
    print("\nUpdated per-task scores:")
    for tr in sorted(result["task_results"], key=lambda x: x["task_id"]):
        old_j = "N/A"
        print(f"  {tr['task_id']}: score={tr['mean_task_score']:.3f}  J={tr['mean_judge_score']:.3f}  judged={tr['judged_runs']}/3  errors={tr['judge_error_count']}")


if __name__ == "__main__":
    asyncio.run(main())
