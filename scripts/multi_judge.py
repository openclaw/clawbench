"""Re-judge all cached runs with multiple judge models.

Judges: Sonnet 4.6 (baseline), GPT 5.4 (OpenRouter), Gemini 3.1 Pro (Google).
Stores per-judge results and prints comparison.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "run_cache"
TASK_DIRS = [ROOT / "tasks" / f"tier{i}" for i in range(1, 6)]
OUTPUT_DIR = ROOT / "data" / "judge_comparison"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── API setup ────────────────────────────────────────────────────────────
def get_keys() -> dict:
    cfg = json.loads((Path.home() / ".openclaw" / "openclaw.json").read_text())
    env = cfg.get("env", {})
    return {
        "anthropic": env.get("ANTHROPIC_API_KEY", ""),
        "openrouter": env.get("OPENROUTER_API_KEY", ""),
        "google": env.get("GOOGLE_API_KEY", ""),
    }

def load_tasks() -> dict[str, dict]:
    tasks = {}
    for d in TASK_DIRS:
        if not d.exists():
            continue
        for f in d.glob("*.yaml"):
            t = yaml.safe_load(f.read_text())
            tasks[t["id"]] = t
    return tasks

# ── judge prompt (same as clawbench/judge.py) ────────────────────────────
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
        sections.extend([
            "",
            "Deterministic verifier summary:",
            f"- completion assertions: {cr.get('passed_assertions', 0)}/{cr.get('total_assertions', 0)}",
            f"- completion score: {cr.get('score', 0):.3f}",
        ])
        for fail in cr.get("failed_assertions", [])[:6]:
            sections.append(f"  - {fail}")

    if judge.get("include_transcript", True):
        transcript = run.get("transcript", {})
        messages = transcript.get("messages", [])
        lines = []
        for msg in messages[-10:]:
            text = (msg.get("text") or "").strip()
            role = msg.get("role", "").upper()
            if text:
                lines.append(f"[{role}] {text[:500]}")
            for tc in msg.get("tool_calls", [])[:4]:
                state = "ok" if tc.get("success") is not False else "failed"
                lines.append(f"[{role} TOOL] {tc.get('family') or tc.get('name')} ({state})")
        if lines:
            sections.extend(["", "Transcript excerpt:", "\n".join(lines)[:4000]])

    sections.extend([
        "",
        "Scoring guidance:",
        "- 1.0 means the output is fully correct, grounded, and high quality.",
        "- 0.7 means acceptable and usable.",
        "- 0.4 means partial or shaky.",
        "- 0.0 means missing, wrong, unsafe, or hallucinated.",
    ])
    return "\n".join(sections).strip()


def parse_response(raw_text: str) -> dict:
    # Try JSON extraction
    text = raw_text.strip()
    for attempt in [text, text.strip("`").strip(), ""]:
        if not attempt:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                attempt = text[start:end+1]
            else:
                break
        try:
            d = json.loads(attempt)
            if isinstance(d, dict):
                return {
                    "score": max(0.0, min(1.0, float(d.get("score", 0)))),
                    "confidence": max(0.0, min(1.0, float(d.get("confidence", 0)))),
                    "error": None,
                }
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    # Fallback: extract numbers
    import re
    m = re.search(r'"?score"?\s*[:=]\s*([0-9.]+)', text)
    if m:
        return {"score": max(0.0, min(1.0, float(m.group(1)))), "confidence": 0.5, "error": None}
    return {"score": 0.0, "confidence": 0.0, "error": "parse_failed"}


# ── judge callers ────────────────────────────────────────────────────────
def call_sonnet(prompt: str, api_key: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return parse_response(resp.content[0].text)


def call_gpt54(prompt: str, api_key: str) -> dict:
    import openai
    client = openai.OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    resp = client.chat.completions.create(
        model="openai/gpt-5.4",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return parse_response(resp.choices[0].message.content)


def call_gemini(prompt: str, api_key: str) -> dict:
    import requests
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:generateContent?key={api_key}",
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"maxOutputTokens": 1024}},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return parse_response(text)


JUDGE_CALLERS = {
    "sonnet-4.6": ("anthropic", call_sonnet),
    "gpt-5.4": ("openrouter", call_gpt54),
    "gemini-3.1-pro": ("google", call_gemini),
}


# ── main ─────────────────────────────────────────────────────────────────
def main():
    keys = get_keys()
    tasks = load_tasks()
    print(f"Loaded {len(tasks)} tasks")

    # Collect all cached runs
    runs = []
    for model_dir in sorted(CACHE_DIR.iterdir()):
        if not model_dir.is_dir():
            continue
        model_name = model_dir.name
        for task_dir in sorted(model_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            task = tasks.get(task_id)
            if not task or not task.get("judge"):
                continue
            for run_file in sorted(task_dir.glob("run*.json")):
                run_data = json.loads(run_file.read_text())
                prompt = build_judge_prompt(task, run_data)
                runs.append({
                    "model": model_name,
                    "task_id": task_id,
                    "run_file": run_file.name,
                    "prompt": prompt,
                    "completion_score": run_data.get("completion_result", {}).get("score", 0),
                })

    print(f"Found {len(runs)} runs to judge")

    # Judge each run with each judge model
    for judge_name, (key_name, caller) in JUDGE_CALLERS.items():
        api_key = keys[key_name]
        if not api_key:
            print(f"\n=== Skipping {judge_name}: no API key ===")
            continue

        output_path = OUTPUT_DIR / f"judge_{judge_name.replace('.', '_')}.json"
        # Resume from existing results
        existing = {}
        if output_path.exists():
            existing = json.loads(output_path.read_text())

        print(f"\n=== Judging with {judge_name} ({len(runs)} runs) ===")
        results = dict(existing)
        remaining = []
        for r in runs:
            rkey = f"{r['model']}/{r['task_id']}/{r['run_file']}"
            if rkey not in results:
                remaining.append((rkey, r))

        if not remaining:
            print(f"  All {len(runs)} already judged, skipping")
            continue

        print(f"  {len(remaining)} remaining ({len(results)} cached)")

        def judge_one(item):
            rkey, r = item
            try:
                result = caller(r["prompt"], api_key)
                result["completion_score"] = r["completion_score"]
                result["model"] = r["model"]
                result["task_id"] = r["task_id"]
                return rkey, result
            except Exception as exc:
                return rkey, {"score": 0.0, "confidence": 0.0, "error": str(exc)[:200],
                              "completion_score": r["completion_score"],
                              "model": r["model"], "task_id": r["task_id"]}

        done = 0
        errors = 0
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(judge_one, item): item for item in remaining}
            for future in as_completed(futures):
                rkey, result = future.result()
                results[rkey] = result
                done += 1
                if result.get("error"):
                    errors += 1
                if done % 20 == 0 or done == len(remaining):
                    print(f"  [{done}/{len(remaining)}] errors={errors}", flush=True)
                    # Save progress
                    output_path.write_text(json.dumps(results, indent=2))

        # Final save
        output_path.write_text(json.dumps(results, indent=2))
        print(f"  Done: {done} judged, {errors} errors. Saved to {output_path}")

    # ── comparison ───────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("JUDGE MODEL COMPARISON")
    print("=" * 80)

    judge_data = {}
    for judge_name in JUDGE_CALLERS:
        path = OUTPUT_DIR / f"judge_{judge_name.replace('.', '_')}.json"
        if path.exists():
            judge_data[judge_name] = json.loads(path.read_text())

    if len(judge_data) < 2:
        print("Need at least 2 judge results for comparison")
        return

    # Per-model comparison
    models = sorted(set(r["model"] for results in judge_data.values() for r in results.values()))
    task_ids = sorted(set(r["task_id"] for results in judge_data.values() for r in results.values()))

    print(f"\n### Overall Judge Score by Evaluated Model")
    print(f"{'Model':<35}", end="")
    for jn in judge_data:
        print(f"  {jn:>14}", end="")
    print()
    print("-" * (35 + 16 * len(judge_data)))

    overall_by_judge = defaultdict(list)
    for model in models:
        print(f"{model:<35}", end="")
        for jn, jresults in judge_data.items():
            scores = [r["score"] for r in jresults.values()
                      if r.get("model") == model and not r.get("error")]
            mean = sum(scores) / len(scores) if scores else 0
            print(f"  {mean:>14.3f}", end="")
            overall_by_judge[jn].append(mean)
        print()

    print(f"{'OVERALL MEAN':<35}", end="")
    for jn in judge_data:
        vals = overall_by_judge[jn]
        print(f"  {sum(vals)/len(vals):>14.3f}", end="")
    print()

    # Per-task comparison (averaged across all models)
    print(f"\n### Per-Task Judge Score (averaged across models)")
    print(f"{'Task':<40}", end="")
    for jn in judge_data:
        print(f"  {jn:>14}", end="")
    print(f"  {'Max Delta':>10}")
    print("-" * (40 + 16 * len(judge_data) + 12))

    task_deltas = []
    for task_id in task_ids:
        print(f"{task_id:<40}", end="")
        task_means = []
        for jn, jresults in judge_data.items():
            scores = [r["score"] for r in jresults.values()
                      if r.get("task_id") == task_id and not r.get("error")]
            mean = sum(scores) / len(scores) if scores else 0
            print(f"  {mean:>14.3f}", end="")
            task_means.append(mean)
        delta = max(task_means) - min(task_means) if task_means else 0
        task_deltas.append((task_id, delta))
        print(f"  {delta:>10.3f}")

    # Summary stats
    judge_names = list(judge_data.keys())
    all_scores_by_judge = {}
    for jn, jresults in judge_data.items():
        all_scores_by_judge[jn] = [r["score"] for r in jresults.values() if not r.get("error")]

    print(f"\n### Summary Statistics")
    print(f"{'Metric':<35}", end="")
    for jn in judge_names:
        print(f"  {jn:>14}", end="")
    print()
    print("-" * (35 + 16 * len(judge_names)))

    for label, fn in [("Mean", lambda s: sum(s)/len(s)),
                       ("Median", lambda s: sorted(s)[len(s)//2]),
                       ("Std Dev", lambda s: (sum((x - sum(s)/len(s))**2 for x in s)/len(s))**0.5),
                       ("% Zero", lambda s: sum(1 for x in s if x == 0)/len(s)*100),
                       ("% >= 0.7", lambda s: sum(1 for x in s if x >= 0.7)/len(s)*100),
                       ("Count", lambda s: len(s)),
                       ("Errors", lambda s: 0)]:
        print(f"{label:<35}", end="")
        for jn in judge_names:
            scores = all_scores_by_judge[jn]
            if label == "Errors":
                errs = sum(1 for r in judge_data[jn].values() if r.get("error"))
                print(f"  {errs:>14}", end="")
            else:
                print(f"  {fn(scores):>14.2f}", end="")
        print()

    # Correlation between judges
    print(f"\n### Pairwise Correlation (Pearson r)")
    import math
    for i, jn1 in enumerate(judge_names):
        for jn2 in judge_names[i+1:]:
            # Align by run key
            common_keys = set(judge_data[jn1].keys()) & set(judge_data[jn2].keys())
            s1 = [judge_data[jn1][k]["score"] for k in common_keys if not judge_data[jn1][k].get("error") and not judge_data[jn2][k].get("error")]
            s2 = [judge_data[jn2][k]["score"] for k in common_keys if not judge_data[jn1][k].get("error") and not judge_data[jn2][k].get("error")]
            if len(s1) < 2:
                print(f"  {jn1} vs {jn2}: insufficient data")
                continue
            n = len(s1)
            mean1, mean2 = sum(s1)/n, sum(s2)/n
            cov = sum((a - mean1) * (b - mean2) for a, b in zip(s1, s2)) / n
            std1 = (sum((x - mean1)**2 for x in s1) / n) ** 0.5
            std2 = (sum((x - mean2)**2 for x in s2) / n) ** 0.5
            r = cov / (std1 * std2) if std1 > 0 and std2 > 0 else 0
            print(f"  {jn1} vs {jn2}: r = {r:.4f} (n={n})")

    # Biggest disagreements
    print(f"\n### Largest Judge Disagreements (top 10 tasks by max delta)")
    task_deltas.sort(key=lambda x: -x[1])
    for task_id, delta in task_deltas[:10]:
        scores_str = "  ".join(
            f"{jn}: {sum(r['score'] for r in jresults.values() if r.get('task_id') == task_id and not r.get('error')) / max(1, sum(1 for r in jresults.values() if r.get('task_id') == task_id and not r.get('error'))):.3f}"
            for jn, jresults in judge_data.items()
        )
        print(f"  {task_id}: delta={delta:.3f}  ({scores_str})")


if __name__ == "__main__":
    main()
