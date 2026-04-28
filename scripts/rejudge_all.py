"""Re-judge ALL judge-infra-failure runs across all models in a drift sweep dir.

Fixes: 'Gateway is restarting', 'Judge execution failed', empty-reason 0-score
judge results by re-running the judge via direct Anthropic API calls (bypassing
the gateway that was failing in the first place).

Updates:
  - data/run_cache_archive/<sweep_tag>/<model>/<task>/runN.json  (in place)
  - data/drift_*/docker_<label>_<tag>.json                       (aggregates)

Usage:
  python3 scripts/rejudge_all.py \
    --drift-dir data/drift_2026-04-19-full \
    --archive-dir data/run_cache_archive/v2026-4-19-full \
    [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import anthropic
import yaml


ROOT = Path(__file__).resolve().parent.parent
TASK_DIRS = [ROOT / "tasks" / f"tier{i}" for i in range(1, 6)]

FAILURE_PHRASES = [
    "gateway is restarting",
    "judge execution failed",
    "judge failed to run",
    "judge call failed",
    "judge timed out",
]

# Weights copied from clawbench/scorer.py
WEIGHTS_DETERMINISTIC = {"completion": 0.40, "trajectory": 0.30, "behavior": 0.20}
WEIGHTS_WITH_JUDGE = {"completion": 0.40, "trajectory": 0.30, "behavior": 0.20, "judge": 0.10}
WEIGHTS_SEMANTIC_ONLY = {"completion": 0.20, "trajectory": 0.20, "behavior": 0.10, "judge": 0.50}
DETERMINISTIC_FLOOR = 0.9999

# Cache-sub → model label (for result JSON lookup)
CACHE_TO_LABEL = {
    "openrouter_z-ai_glm-5.1": "glm",
    "openrouter_minimax_minimax-m2.7": "minimax",
    "openrouter_moonshotai_kimi-k2.5": "kimi",
    "openrouter_qwen_qwen3.6-plus": "qwen",
    "anthropic_claude-opus-4-6": "opus46",
    "anthropic_claude-opus-4-7": "opus47",
    "anthropic_claude-sonnet-4-6": "sonnet46",
    "openai_gpt-5.4": "gpt54",
    "openai_gpt-5.2": "gpt52",
    "google_gemini-3.1-pro-preview": "gemini",
}


def get_api_key() -> str:
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return k
    cfg = Path.home() / ".openclaw" / "openclaw.json"
    if cfg.exists():
        try:
            v = json.loads(cfg.read_text()).get("env", {}).get("ANTHROPIC_API_KEY")
            if v:
                return v
        except Exception:
            pass
    raise RuntimeError("No ANTHROPIC_API_KEY found (set env var or openclaw.json)")


def load_tasks() -> dict[str, dict]:
    out = {}
    for td in TASK_DIRS:
        if not td.exists():
            continue
        for yf in sorted(td.glob("*.yaml")):
            t = yaml.safe_load(yf.read_text())
            if t and "id" in t:
                out[t["id"]] = t
    return out


def is_judge_infra_fail(jr: dict) -> bool:
    if not jr or not jr.get("enabled"):
        return False
    reason = (jr.get("reason") or "").lower()
    if any(p in reason for p in FAILURE_PHRASES):
        return True
    if jr.get("error"):
        return True
    # Empty reason + score 0 is likely an unreported failure
    if not reason.strip() and jr.get("score", 0) == 0:
        return True
    return False


def render_transcript_excerpt(transcript: dict, max_chars: int = 4000) -> str:
    msgs = transcript.get("messages", []) if transcript else []
    parts = []
    for m in msgs:
        role = m.get("role", "?")
        text = (m.get("text") or "").strip()
        if text:
            parts.append(f"[{role}] {text[:500]}")
        for tc in (m.get("tool_calls") or []):
            parts.append(f"[{role}/tool] {tc.get('name','?')}({json.dumps(tc.get('arguments',{}))[:120]})")
        if m.get("tool_result_for"):
            tr = (m.get("tool_result_content") or "")
            parts.append(f"[tool_result] {tr[:300]}")
    excerpt = "\n".join(parts)
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars] + "\n... (truncated)"
    return excerpt


def build_judge_prompt(task: dict, run: dict) -> str:
    rubric = task.get("judge", {}).get("rubric", "").strip()
    transcript_excerpt = render_transcript_excerpt(run.get("transcript", {}))
    cr = run.get("completion_result", {})
    comp_summary = (
        f"score={cr.get('score',0):.3f}  "
        f"passed={cr.get('passed_assertions',0)}/{cr.get('total_assertions',0)}"
    )
    failures = cr.get("failed_assertions", [])
    comp_feedback = "\n".join(f"- {f}" for f in failures[:5]) if failures else "(none)"
    return (
        f"{rubric}\n\n"
        f"=== Completion verifier summary ===\n{comp_summary}\n"
        f"Failed assertions:\n{comp_feedback}\n\n"
        f"=== Transcript excerpt ===\n{transcript_excerpt}\n"
    )


JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_judge_response(raw: str, threshold: float) -> dict:
    try:
        # Find the first balanced JSON object (json.raw_decode tolerates trailing text)
        start = raw.find("{")
        if start < 0:
            raise ValueError("no JSON in response")
        decoder = json.JSONDecoder()
        obj, _end = decoder.raw_decode(raw[start:])
        score = float(obj.get("score", 0))
        confidence = float(obj.get("confidence", 0.5))
        reason = str(obj.get("reason", ""))
        return {
            "enabled": True,
            "score": round(max(0.0, min(1.0, score)), 4),
            "confidence": round(max(0.0, min(1.0, confidence)), 4),
            "reason": reason,
            "rubric_hits": obj.get("rubric_hits") or [],
            "rubric_misses": obj.get("rubric_misses") or [],
            "passing_threshold": threshold,
            "passed": score >= threshold,
            "error": None,
        }
    except Exception as exc:
        return {
            "enabled": True, "score": 0.0, "confidence": 0.0,
            "reason": f"parse failed: {exc}", "rubric_hits": [], "rubric_misses": [],
            "passing_threshold": threshold, "passed": False, "error": str(exc),
        }


def combine_run_score(c: float, t: float, b: float, j: Optional[float], has_det: bool) -> float:
    if j is None:
        w = WEIGHTS_DETERMINISTIC
        ws = w["completion"]*c + w["trajectory"]*t + w["behavior"]*b
        return round(min(1.0, max(0.0, ws/sum(w.values()))), 4)
    if has_det:
        if c < DETERMINISTIC_FLOOR:
            w = WEIGHTS_DETERMINISTIC
            ws = w["completion"]*c + w["trajectory"]*t + w["behavior"]*b
            return round(min(1.0, max(0.0, ws/sum(w.values()))), 4)
        w = WEIGHTS_WITH_JUDGE
        ws = w["completion"]*c + w["trajectory"]*t + w["behavior"]*b + w["judge"]*j
        return round(min(1.0, max(0.0, ws)), 4)
    w = WEIGHTS_SEMANTIC_ONLY
    ws = w["completion"]*c + w["trajectory"]*t + w["behavior"]*b + w["judge"]*j
    return round(min(1.0, max(0.0, ws)), 4)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drift-dir", required=True, type=Path)
    ap.add_argument("--archive-dir", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.archive_dir.exists():
        print(f"Archive dir missing: {args.archive_dir}")
        sys.exit(1)

    tasks = load_tasks()
    print(f"Loaded {len(tasks)} task definitions")

    # Gather all affected runs: (cache_sub, task_id, run_path, run_data)
    affected: list = []
    for model_dir in sorted(args.archive_dir.iterdir()):
        if not model_dir.is_dir():
            continue
        if model_dir.name not in CACHE_TO_LABEL:
            continue
        for task_dir in model_dir.iterdir():
            if not task_dir.is_dir():
                continue
            for rf in sorted(task_dir.glob("run*.json")):
                try:
                    run = json.loads(rf.read_text())
                except Exception:
                    continue
                if is_judge_infra_fail(run.get("judge_result", {})):
                    affected.append((model_dir.name, task_dir.name, rf, run))

    print(f"Found {len(affected)} runs with judge infra failures")
    if args.dry_run:
        from collections import Counter
        by_model = Counter(a[0] for a in affected)
        for m, n in by_model.most_common():
            print(f"  {m}: {n}")
        return
    if not affected:
        return

    api_key = get_api_key()
    client = anthropic.Anthropic(api_key=api_key)

    # Re-judge each
    succ = 0
    fail = 0
    for i, (cache_sub, task_id, rp, run) in enumerate(affected):
        task = tasks.get(task_id)
        if not task or not task.get("judge"):
            continue
        prompt = build_judge_prompt(task, run)
        threshold = task["judge"].get("passing_threshold", 0.7)
        print(f"[{i+1}/{len(affected)}] {cache_sub}/{task_id}/{rp.name} ... ", end="", flush=True)
        try:
            t0 = time.monotonic()
            resp = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text
            dur_ms = int((time.monotonic() - t0) * 1000)
            parsed = parse_judge_response(raw, threshold)
            parsed["model"] = "anthropic/claude-sonnet-4-6"
            parsed["duration_ms"] = dur_ms
            parsed["token_usage"] = {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            }
            parsed["rejudged_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            run["judge_result"] = parsed
            # Recompute run_score
            cr = run.get("completion_result", {})
            tr = run.get("trajectory_result", {})
            br = run.get("behavior_result", {})
            has_det = cr.get("total_assertions", 0) > 0
            j = parsed["score"] if parsed["enabled"] and not parsed.get("error") else None
            old_rs = run.get("run_score", 0)
            new_rs = combine_run_score(cr.get("score", 0), tr.get("score", 0), br.get("score", 0), j, has_det)
            run["run_score"] = new_rs
            tmp = rp.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(run, indent=2))
            tmp.replace(rp)
            print(f"J={parsed['score']:.2f} ΔRS={new_rs - old_rs:+.3f}")
            succ += 1
        except Exception as exc:
            print(f"ERROR: {exc}")
            fail += 1

    print(f"\nRe-judging complete: {succ} succeeded, {fail} failed")


if __name__ == "__main__":
    main()
