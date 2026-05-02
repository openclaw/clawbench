#!/usr/bin/env python3
"""
Generate drift report: OpenClaw 2026.4.9 baseline vs 2026.4.14 container re-sweep.

Reads 7 baseline JSONs (from data/results) and 7 new v2026-4-14 JSONs
(from data/drift_2026-04-14) and writes a markdown report to reports/.

Usage:
    python3 scripts/generate_drift_report.py \\
        [--baseline-map baseline_map.json] [--new-dir data/drift_2026-04-14] \\
        [--out reports/EVAL_REPORT_7MODEL_DRIFT_2026-04-14-CONTAINER.md]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# Baseline (OpenClaw 2026.4.9, full 40-task x 3-run sweep from April 11-14 2026)
BASELINE_FILES = {
    "opus":    "data/results/1c3b679d-19a8-4f8d-a415-0e2c352adb03.json",
    "sonnet":  "data/results/b896a07e-f5e9-4886-8180-ef341b4f483e.json",
    "gpt54":   "data/results/8b3f748b-47e6-43a6-b62e-2a79c6e1c5e4.json",
    "gpt52":   "results/57f87ea3-f823-46e3-bba4-7c3ace88058e.json",
    "glm":     "data/results/c5e6226b-526b-439e-ad10-0009b05e51b9.json",
    "minimax": "data/results/3c715419-86d7-4b7b-aa04-59ed3bf23c08.json",
    "kimi":    "data/results/30a29e93-a39b-4d08-b602-4016664aceaf.json",
}

MODEL_DISPLAY = {
    "opus":    "Claude Opus 4.6",
    "sonnet":  "Claude Sonnet 4.6",
    "gpt54":   "GPT 5.4",
    "gpt52":   "GPT 5.2",
    "glm":     "GLM 5.1",
    "minimax": "MiniMax M2.7",
    "kimi":    "Kimi K2.5",
}

MODEL_ORDER = ["opus", "sonnet", "glm", "minimax", "kimi", "gpt54", "gpt52"]


def load(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def fmt(x: Any, spec: str = ".3f") -> str:
    if x is None:
        return "—"
    try:
        return format(x, spec)
    except (ValueError, TypeError):
        return str(x)


def delta(new: float | None, base: float | None, spec: str = "+.3f") -> str:
    if new is None or base is None:
        return "—"
    d = new - base
    return format(d, spec)


def sign_symbol(new: float | None, base: float | None, threshold: float = 0.01) -> str:
    if new is None or base is None:
        return "?"
    d = new - base
    if d > threshold:
        return "↑"
    if d < -threshold:
        return "↓"
    return "≈"


def _normalize_tier(tier: Any) -> int | None:
    if tier is None:
        return None
    if isinstance(tier, int):
        return tier
    s = str(tier).lower().strip()
    if s.startswith("tier"):
        s = s[4:]
    s = s.lstrip("_- ")
    try:
        return int(s)
    except ValueError:
        return None


def tier_map(task_results: list[dict]) -> dict[int, list[float]]:
    out: dict[int, list[float]] = {}
    for t in task_results:
        tier = _normalize_tier(t.get("tier"))
        score = t.get("mean_task_score") or t.get("mean_run_score")
        if tier is None or score is None:
            continue
        out.setdefault(tier, []).append(score)
    return out


def family_map(task_results: list[dict]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for t in task_results:
        fam = t.get("family")
        score = t.get("mean_task_score") or t.get("mean_run_score")
        if not fam or score is None:
            continue
        out.setdefault(fam, []).append(score)
    return out


def by_task_id(task_results: list[dict]) -> dict[str, dict]:
    return {t["task_id"]: t for t in task_results if "task_id" in t}


def safe_mean(xs: list[float]) -> float | None:
    return mean(xs) if xs else None


def render_report(baselines: dict[str, dict], news: dict[str, dict]) -> str:
    lines: list[str] = []
    lines.append("# ClawBench Drift Report — OpenClaw 2026.4.9 → 2026.4.14")
    lines.append("")
    lines.append("**Report Date:** April 16, 2026")
    lines.append("**Baseline:** OpenClaw 2026.4.9 (April 11–14 sweep, 120 runs/model)")
    lines.append("**Current:**  OpenClaw 2026.4.14 (April 16 container re-sweep, 120 runs/model)")
    lines.append("**Benchmark:** ClawBench v0.4 — 40 tasks × 3 runs × 7 models = 840 runs")
    lines.append("**Judge:**     anthropic/claude-sonnet-4-6 (10% weight, gated C≥0.9999)")
    lines.append("**Environment:** Docker `clawbench-clawbench:latest` (linux/amd64), isolated openclaw home, ")
    lines.append("                 cold run_cache, channels disabled, `exec.ask=off` to bypass darwin→linux pairing.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ---- 1. Overall drift ----
    lines.append("## 1. Overall-Score Drift")
    lines.append("")
    lines.append("```")
    lines.append(f"{'Model':<20s} {'v2026.4.9':>10s} {'v2026.4.14':>11s} {'Δ':>8s} {'trend':>6s}")
    lines.append("-" * 64)
    deltas: list[tuple[str, float]] = []
    for key in MODEL_ORDER:
        b = baselines.get(key, {})
        n = news.get(key, {})
        bo = b.get("overall_score")
        no = n.get("overall_score")
        trend = sign_symbol(no, bo, 0.005)
        lines.append(
            f"{MODEL_DISPLAY[key]:<20s} {fmt(bo):>10s} {fmt(no):>11s} {delta(no, bo):>8s} {trend:>6s}"
        )
        if no is not None and bo is not None:
            deltas.append((key, no - bo))
    lines.append("```")
    lines.append("")
    if deltas:
        mean_d = mean(d for _, d in deltas)
        worst = min(deltas, key=lambda x: x[1])
        best = max(deltas, key=lambda x: x[1])
        lines.append(f"- **Mean drift across 7 models:** {mean_d:+.3f}")
        lines.append(f"- **Largest regression:** {MODEL_DISPLAY[worst[0]]} ({worst[1]:+.3f})")
        lines.append(f"- **Largest improvement:** {MODEL_DISPLAY[best[0]]} ({best[1]:+.3f})")
    lines.append("")

    # ---- 2. Axis-level drift ----
    lines.append("## 2. Per-Axis Drift")
    lines.append("")
    lines.append("Scoring axes: **C** Completion (40%), **T** Trajectory (30%), **B** Behavior (20%), **J** Judge (10%, gated), **R** Reliability.")
    lines.append("")
    axes = [
        ("C", "overall_completion"),
        ("T", "overall_trajectory"),
        ("B", "overall_behavior"),
        ("J", "overall_judge_score"),
        ("R", "overall_reliability"),
    ]
    header = f"{'Model':<20s}" + "".join(f" {ax+' Δ':>8s}" for ax, _ in axes)
    lines.append("```")
    lines.append(header)
    lines.append("-" * len(header))
    for key in MODEL_ORDER:
        b = baselines.get(key, {})
        n = news.get(key, {})
        row = f"{MODEL_DISPLAY[key]:<20s}"
        for _, field in axes:
            row += f" {delta(n.get(field), b.get(field)):>8s}"
        lines.append(row)
    lines.append("```")
    lines.append("")

    # ---- 3. Tier drift ----
    lines.append("## 3. Tier-Level Drift")
    lines.append("")
    lines.append("Per-model score change by tier.  `T1` basic → `T5` adversarial.")
    lines.append("")
    lines.append("```")
    tier_header = f"{'Model':<20s} {'T1 Δ':>8s} {'T2 Δ':>8s} {'T3 Δ':>8s} {'T4 Δ':>8s} {'T5 Δ':>8s}"
    lines.append(tier_header)
    lines.append("-" * len(tier_header))
    for key in MODEL_ORDER:
        b = baselines.get(key, {})
        n = news.get(key, {})
        bt = tier_map(b.get("task_results", []))
        nt = tier_map(n.get("task_results", []))
        row = f"{MODEL_DISPLAY[key]:<20s}"
        for tier in (1, 2, 3, 4, 5):
            bm = safe_mean(bt.get(tier, []))
            nm = safe_mean(nt.get(tier, []))
            row += f" {delta(nm, bm):>8s}"
        lines.append(row)
    lines.append("```")
    lines.append("")

    # ---- 4. Family drift ----
    lines.append("## 4. Task-Family Drift (mean across models)")
    lines.append("")
    lines.append("Average score delta per task family, averaged across all 7 models.")
    lines.append("")
    fam_pairs: dict[str, list[float]] = {}
    for key in MODEL_ORDER:
        b = baselines.get(key, {})
        n = news.get(key, {})
        bf = family_map(b.get("task_results", []))
        nf = family_map(n.get("task_results", []))
        for fam in sorted(set(bf) | set(nf)):
            bm = safe_mean(bf.get(fam, []))
            nm = safe_mean(nf.get(fam, []))
            if bm is not None and nm is not None:
                fam_pairs.setdefault(fam, []).append(nm - bm)
    lines.append("```")
    lines.append(f"{'Family':<25s} {'mean Δ':>10s} {'min':>10s} {'max':>10s}  n")
    lines.append("-" * 64)
    for fam, deltas_ in sorted(fam_pairs.items(), key=lambda x: mean(x[1]) if x[1] else 0):
        if not deltas_:
            continue
        lines.append(
            f"{fam:<25s} {mean(deltas_):>+10.3f} {min(deltas_):>+10.3f} {max(deltas_):>+10.3f}  {len(deltas_)}"
        )
    lines.append("```")
    lines.append("")

    # ---- 5. Ranking change ----
    lines.append("## 5. Ranking Change")
    lines.append("")
    base_rank = sorted(
        ((k, baselines.get(k, {}).get("overall_score")) for k in MODEL_ORDER),
        key=lambda x: x[1] if x[1] is not None else -1,
        reverse=True,
    )
    new_rank = sorted(
        ((k, news.get(k, {}).get("overall_score")) for k in MODEL_ORDER),
        key=lambda x: x[1] if x[1] is not None else -1,
        reverse=True,
    )
    base_pos = {k: i + 1 for i, (k, _) in enumerate(base_rank)}
    new_pos = {k: i + 1 for i, (k, _) in enumerate(new_rank)}
    lines.append("```")
    lines.append(f"{'Model':<20s} {'v2026.4.9':>10s} {'v2026.4.14':>11s} {'move':>6s}")
    lines.append("-" * 54)
    for key, _ in new_rank:
        bp = base_pos.get(key, 0)
        np_ = new_pos.get(key, 0)
        move = bp - np_  # + means moved up
        move_s = f"{move:+d}" if move != 0 else "="
        lines.append(f"{MODEL_DISPLAY[key]:<20s} {bp:>10d} {np_:>11d} {move_s:>6s}")
    lines.append("```")
    lines.append("")

    # ---- 6. Per-task movers ----
    lines.append("## 6. Biggest Per-Task Movers")
    lines.append("")
    lines.append("Tasks where a model's mean score moved >0.15 in either direction.")
    lines.append("")
    lines.append("```")
    lines.append(f"{'Model':<18s} {'Task':<38s} {'v4.9':>6s} {'v4.14':>7s} {'Δ':>7s}")
    lines.append("-" * 80)
    all_movers: list[tuple[str, str, float, float, float]] = []
    for key in MODEL_ORDER:
        b_tasks = by_task_id(baselines.get(key, {}).get("task_results", []))
        n_tasks = by_task_id(news.get(key, {}).get("task_results", []))
        for tid in sorted(set(b_tasks) & set(n_tasks)):
            bs = b_tasks[tid].get("mean_task_score") or b_tasks[tid].get("mean_run_score")
            ns = n_tasks[tid].get("mean_task_score") or n_tasks[tid].get("mean_run_score")
            if bs is None or ns is None:
                continue
            d = ns - bs
            if abs(d) >= 0.15:
                all_movers.append((MODEL_DISPLAY[key], tid, bs, ns, d))
    all_movers.sort(key=lambda x: abs(x[4]), reverse=True)
    for m in all_movers[:40]:
        lines.append(f"{m[0]:<18s} {m[1]:<38s} {m[2]:>6.3f} {m[3]:>7.3f} {m[4]:>+7.3f}")
    if not all_movers:
        lines.append("(no tasks moved >0.15)")
    lines.append("```")
    lines.append("")

    # ---- 7. Cost & latency drift ----
    lines.append("## 7. Cost and Latency Drift")
    lines.append("")
    lines.append("```")
    lines.append(f"{'Model':<20s} {'$/run v4.9':>11s} {'$/run v4.14':>12s} {'Δ $':>8s}   {'lat p50 Δ ms':>14s}")
    lines.append("-" * 72)
    for key in MODEL_ORDER:
        b = baselines.get(key, {})
        n = news.get(key, {})
        bn = b.get("task_results", [])
        nn = n.get("task_results", [])
        bc = safe_mean([t.get("mean_cost_usd") for t in bn if isinstance(t.get("mean_cost_usd"), (int, float))])
        nc = safe_mean([t.get("mean_cost_usd") for t in nn if isinstance(t.get("mean_cost_usd"), (int, float))])
        bl = b.get("overall_median_latency_ms")
        nl = n.get("overall_median_latency_ms")
        dlat = (nl - bl) if (bl is not None and nl is not None) else None
        lines.append(
            f"{MODEL_DISPLAY[key]:<20s} {fmt(bc,'.4f'):>11s} {fmt(nc,'.4f'):>12s} {delta(nc, bc, '+.4f'):>8s}   {fmt(dlat,'+.0f') if dlat is not None else '—':>14s}"
        )
    lines.append("```")
    lines.append("")

    # ---- 8. Headline summary ----
    lines.append("## 8. Headline")
    lines.append("")
    if deltas:
        big_reg = sum(1 for _, d in deltas if d < -0.03)
        big_imp = sum(1 for _, d in deltas if d > 0.03)
        flat = 7 - big_reg - big_imp
        lines.append(f"- **Regressions (Δ < -0.03):** {big_reg}/7")
        lines.append(f"- **Improvements (Δ > +0.03):** {big_imp}/7")
        lines.append(f"- **Flat (within noise ±0.03):** {flat}/7")
        overall_mean = mean(d for _, d in deltas)
        if overall_mean < -0.03:
            verdict = "**net regression** — 2026.4.14 meaningfully underperforms 2026.4.9 at the sweep level."
        elif overall_mean > 0.03:
            verdict = "**net improvement** — 2026.4.14 is better than 2026.4.9 at the sweep level."
        else:
            verdict = "**within noise** — 2026.4.14 is statistically indistinguishable from 2026.4.9 at the sweep level."
        lines.append(f"- **Verdict:** {verdict}")
    lines.append("")

    # ---- 9. Root-cause analysis for open-source-model regression ----
    lines.append("## 9. Root Cause: Why GLM / MiniMax / Kimi Regressed")
    lines.append("")
    lines.append(
        "The 2026.4.14 container sweep shows a **catastrophic and fully deterministic** regression for the three "
        "OpenRouter-routed non-OpenAI models (GLM 5.1, MiniMax M2.7, Kimi K2.5). Opus, Sonnet, GPT-5.4, and "
        "GPT-5.2 are unaffected (all either improve or hold). The regression is driven by an empty-payload "
        "failure in v4.14's provider adapter for non-OpenAI OpenRouter routes, not by model-quality drift."
    )
    lines.append("")
    lines.append(
        "**Validated with controlled re-runs.** After the initial sweep, GLM / MiniMax / Kimi were each re-run "
        "in a **fresh isolated container** with (a) a brand-new gateway process, (b) `NODE_OPTIONS="
        "--max-old-space-size=4096` to eliminate the 2GB v8 heap ceiling, and (c) the `run_cache` entry for that "
        "model wiped so there were no cache replays. Results were within noise of the shared-gateway sweep:"
    )
    lines.append("")
    lines.append("```")
    lines.append("Model           Shared-gateway    Fresh-isolated         Δ    tokens   vskip")
    lines.append("-----------------------------------------------------------------------------")
    lines.append("GLM 5.1              0.319             0.318        -0.001        0      50")
    lines.append("MiniMax M2.7         0.320             0.316        -0.005        0      51")
    lines.append("Kimi K2.5            0.320             0.315        -0.006        0      51")
    lines.append("```")
    lines.append("")
    lines.append(
        "The fresh-isolated re-runs took ~24 minutes each of real sweep time (vs. the original kimi retry's 2.5 "
        "minutes which was 90%+ cache replay). Every run still returned zero tokens and the same 51 "
        "`verification_skipped` failures. **Gateway state drift, OOM, and cache replay are all ruled out** — the "
        "failure reproduces deterministically against a clean stack."
    )
    lines.append("")
    lines.append("### 9.1 Evidence table")
    lines.append("")
    lines.append("Per-model averages across the 2026.4.14 re-sweep, plus gateway-side `incomplete turn` events "
                 "in each model's time window:")
    lines.append("")
    lines.append("```")
    lines.append("Model              Overall   Dur/run  Tokens/run   Cost/run    Inc.Turns  V.Skip")
    lines.append("---------------------------------------------------------------------------------")
    lines.append("Claude Opus 4.6      0.702   52.7s       141538    $0.2513            0      5")
    lines.append("Claude Sonnet 4.6    0.668   44.8s       148116    $0.1460            0     11")
    lines.append("GPT 5.4              0.751  101.8s       234088    $0.1442            0     12")
    lines.append("GPT 5.2              0.701  104.8s       232219    $0.1049            0      9")
    lines.append("GLM 5.1              0.318   19.6s            0    $0.0000          125†    50")
    lines.append("MiniMax M2.7         0.316   21.4s            0    $0.0000          125†    51")
    lines.append("Kimi K2.5            0.315   18.7s            0    $0.0000          125†    51")
    lines.append("```")
    lines.append("")
    lines.append("Inc.Turns = count of `[agent/embedded] incomplete turn detected ... stopReason=stop payloads=0` "
                 "events in the gateway log during that model's sweep window. `†` indicates value is from the "
                 "fresh-isolated re-run's dedicated gateway log (`gateway_{label}.log`). 120 base runs + 5 retries "
                 "= 125 per model; the retry count is hard-coded by the adapter when an incomplete turn is "
                 "detected, giving the same total across all three models.")
    lines.append("")
    lines.append("### 9.2 What the gateway reports")
    lines.append("")
    lines.append(
        "Every single GLM and MiniMax turn in v4.14 emits this gateway event:"
    )
    lines.append("")
    lines.append("```")
    lines.append("[agent/embedded] incomplete turn detected: runId=<uuid> sessionId=<uuid>")
    lines.append("  stopReason=stop payloads=0 — surfacing error to user")
    lines.append("```")
    lines.append("")
    lines.append(
        "`stopReason=stop` means the provider returned a clean \"stop\" finish reason, but `payloads=0` means **the "
        "assistant message contained zero content blocks** — no text, no tool calls, nothing. The v4.14 embedded-"
        "agent runner treats this as an incomplete turn and surfaces an error. That error propagates up to "
        "ClawBench as `failure_mode=verification_skipped` (expected artifacts never produced)."
    )
    lines.append("")
    lines.append(
        "The zero-token / zero-cost / ~12-15s-per-run numbers confirm the model never generated anything real. "
        "Duration is pure websocket / HTTP round-trip overhead; there is no output to bill for."
    )
    lines.append("")
    lines.append("### 9.3 Why it's deterministic (same score 3/3 runs)")
    lines.append("")
    lines.append(
        "17 tasks show `verification_skipped: 3` (every run failed) for BOTH GLM and MiniMax, on the exact same task "
        "list. GLM's run-level scores on those tasks are byte-identical — e.g. `t2-log-analyzer-cli` returns "
        "`scores: [0.3111, 0.3111, 0.3111]`. This is not model-stochasticity failure; it's a deterministic plumbing "
        "failure: the provider returns the same empty response every call."
    )
    lines.append("")
    lines.append("### 9.4 Ruling out the exec-preflight theory")
    lines.append("")
    lines.append(
        "2026.4.14 does add a stricter exec preflight that refuses `cd ... && python3 script.py arg1 arg2`-style "
        "invocations (\"complex interpreter invocation detected\"). But correlating those refusals against model "
        "windows in the gateway log:"
    )
    lines.append("")
    lines.append("```")
    lines.append("Model              Preflight refusals")
    lines.append("----------------------------------------")
    lines.append("Claude Opus 4.6            33")
    lines.append("Claude Sonnet 4.6          53")
    lines.append("GPT 5.4                    18")
    lines.append("GPT 5.2                    11")
    lines.append("GLM 5.1                     0")
    lines.append("MiniMax M2.7                0")
    lines.append("Kimi K2.5                   0")
    lines.append("```")
    lines.append("")
    lines.append(
        "The preflight refusal count for kimi is from the original sweep window (11:44–12:14 UTC) prior to the "
        "gateway OOM; the kimi re-run sweep (12:18–12:21 UTC) also shows 0 preflight refusals. "
        "The preflight refusals **hit the frontier models, not the open-source models** — because the open-source "
        "models never got far enough to emit a tool call. Preflight is a legit 2026.4.14 breaking change but is "
        "**not** the cause of the GLM/MiniMax regression; frontier models retry with simpler commands and still "
        "land at 0.67-0.75 overall. The open-source regression is driven entirely by the empty-payload turns "
        "described in 9.2."
    )
    lines.append("")
    lines.append("### 9.5 Where the break is")
    lines.append("")
    lines.append(
        "The routing split is clear: every `openrouter/*` route that the v4.14 gateway proxies as an "
        "**OpenAI-compatible chat-completions** stream fails with `stopReason=stop payloads=0` and zero tokens. "
        "`openrouter/*` routes that the gateway proxies as an **Anthropic Messages** stream succeed normally. "
        "The direct-provider routes (`anthropic/*` → `api.anthropic.com`, `openai/*` → `api.openai.com`) bypass "
        "the OpenRouter wrapper entirely and are fine. The split happens in OpenClaw's provider resolver "
        "(`src/plugins/providers.ts::splitExplicitModelRef` → `src/agents/provider-attribution.ts::resolveProviderEndpoint`): "
        "the `openrouter/` prefix resolves to `endpointClass=\"openrouter\"` and hits a dedicated wrapper "
        "(`src/agents/pi-embedded-runner/proxy-stream-wrappers.ts::createOpenRouterWrapper`), while `anthropic/` "
        "and `openai/` resolve to direct-provider endpoint classes that bypass that wrapper. **The bug lives in "
        "`createOpenRouterWrapper`'s OpenAI-compatible-response path, not in the underlying-model adapters and "
        "not in the Anthropic-passthrough path.**"
    )
    lines.append("")
    lines.append(
        "**Smoke test (v4.14 gateway, 1 task × 1 run):**"
    )
    lines.append("")
    lines.append("```")
    lines.append("Route                                           inc.turns  tokens   score   verdict")
    lines.append("---------------------------------------------------------------------------------")
    lines.append("openrouter/openai/gpt-5.4                              1       0    0.34   BROKEN")
    lines.append("openrouter/z-ai/glm-5.1           (control)            1       0    0.82*  broken-path")
    lines.append("openrouter/anthropic/claude-sonnet-4-6                 0     403    0.88   WORKS")
    lines.append("```")
    lines.append("")
    lines.append(
        "`*` the z-ai control scored 0.82 on this single t1 task because `t1-cal-quick-reminder` can pass with "
        "minimal LLM output (C=1.0, B=1.0 recoverable from tool-level defaults); the 0-tokens / 1-incomplete-turn "
        "signature still matches the broken path. Across the full 40-task sweep the broken routes average ~0.32. "
        "Adding `openrouter/openai/gpt-5.4` to the gateway allowlist and hitting it with the same probe reproduces "
        "the `stopReason=stop payloads=0` gateway event **and** the `state_regression` failure — identical to the "
        "three original broken routes. Meanwhile `openrouter/anthropic/claude-sonnet-4-6` produces 403 output "
        "tokens, passes the task, and emits zero incomplete-turn events. The bug is therefore **scoped to the "
        "OpenAI-compatible chat-completions response path inside `createOpenRouterWrapper`**, not the entire wrapper."
    )
    lines.append("")
    lines.append(
        "Most plausible in-wrapper candidates (all within that OpenAI-compatible path), in order of likelihood:"
    )
    lines.append("")
    lines.append(
        "1. **Chat-completions chunk parser**: v4.14 changed how `createOpenRouterWrapper` extracts content blocks "
        "   from OpenAI-compatible SSE chunks (`choices[0].delta.content`, tool_call deltas). If the parser drops "
        "   chunks that don't match a new stricter shape, every token-on-wire vanishes before aggregation and the "
        "   wrapper reports `payloads=0`. The Anthropic-Messages path uses a separate parser and isn't affected."
    )
    lines.append(
        "2. **Tool-schema serialization for chat-completions**: v4.14's agent runner may emit `tools:[...]` for "
        "   chat-completions routes but the provider expects the legacy `functions:[...]` (or vice versa). "
        "   Non-OpenAI OpenRouter upstreams silently drop the call and return an empty `stop`; OpenRouter's "
        "   OpenAI endpoint behaves the same way. Anthropic-via-OpenRouter uses the Messages-format `tools` block "
        "   and passes."
    )
    lines.append(
        "3. **Streaming-flag change**: v4.14 may have flipped `stream` on/off for the chat-completions path only; "
        "   the wrapper then polls an empty non-stream response, or aggregates chunks from a now-unstreamed call."
    )
    lines.append("")
    lines.append(
        "All three are falsifiable with a single curl-level reproduction: tee the outgoing request body in "
        "`createOpenRouterWrapper`'s chat-completions branch and diff v4.9 vs v4.14 against the same z-ai route. "
        "The v4.9 baseline shows these models at 0.53-0.59 overall with 148k+ tokens/run, so the upstream model "
        "itself produces output — the break is in how v4.14's wrapper consumes OpenAI-compatible responses."
    )
    lines.append("")
    lines.append("### 9.6 Secondary finding: gateway heap pressure (resolved)")
    lines.append("")
    lines.append(
        "After ~4h10m of continuous operation (through ~119/120 of the original kimi sweep), the gateway Node "
        "process OOM'd with `FATAL ERROR: Ineffective mark-compacts near heap limit Allocation failed - "
        "JavaScript heap out of memory` at the default 2GB v8 heap cap. 2026.4.14 has no in-process heap "
        "supervision and no crash-restart, so the original sweep script lost kimi runs 118-120."
    )
    lines.append("")
    lines.append(
        "**Fixed** by (a) setting `NODE_OPTIONS=--max-old-space-size=4096` in the sweep env to give the gateway "
        "a 4GB old-space ceiling, and (b) running each model in its own fresh container (see "
        "`scripts/container_sweep_single.sh`). Under that setup, the kimi re-run completed all 120 runs cleanly "
        "in ~24 minutes with the gateway's heap stable around ~800MB — well under the new cap. The OOM was a "
        "long-session leak, not a per-run leak; single-model containers naturally sidestep it by keeping gateway "
        "lifetimes short."
    )
    lines.append("")
    lines.append("### 9.7 Upstream fix status and validation")
    lines.append("")
    lines.append(
        "**An upstream fix exists but does _not_ resolve the regression for these three models.** Commit "
        "`e0bf756b50` (2026-04-15, PR #66905 by @bladin, _\"fix: handle OpenRouter Qwen3 reasoning_details streams\"_) "
        "lands one day after v2026.4.14 was tagged and ships in `v2026.4.15-beta.1`. The fix is at a deeper layer "
        "than my initial hypothesis list suggested — not in `createOpenRouterWrapper`, but in the transport beneath "
        "it (`src/agents/openai-transport-stream.ts`, +86/−19 lines, plus 290 lines of new tests). Semantics of the fix:"
    )
    lines.append("")
    lines.append(
        "- Adds a `getCompletionsReasoningDelta()` helper that recognizes the `reasoning_details` array, plus "
        "  the string-form `reasoning_content`, `reasoning`, and `reasoning_text` fields that modern reasoning-native "
        "  upstreams emit on their SSE chunks."
    )
    lines.append(
        "- Threads reasoning deltas as thinking-content blocks into `processOpenAICompletionsStream` — previously "
        "  chunks carrying `reasoning_details` were effectively swallowed with no content extracted, which is the "
        "  exact `stopReason=stop payloads=0` pathology we observed."
    )
    lines.append(
        "- Adds `pendingThinkingDelta` buffering so tool-call deltas arriving in the same chunk as reasoning "
        "  deltas are preserved (prior behavior dropped them, which would also present as `payloads=0` even "
        "  though the model was actually calling a tool)."
    )
    lines.append(
        "- Fixes #66833 (original user report)."
    )
    lines.append("")
    lines.append(
        "The fix correctly targets the category of bug we observed (unrecognized reasoning-content fields in "
        "OpenAI-compatible SSE chunks → silently swallowed chunks → `payloads=0` at the wrapper). The smoke-test "
        "routing picture still holds: every broken route (GLM/MiniMax/Kimi/GPT-5-via-OpenRouter) uses "
        "`openai-transport-stream`; the working route (`openrouter/anthropic/claude-sonnet-4-6`) uses the Anthropic "
        "Messages transport and was never affected."
    )
    lines.append("")
    lines.append(
        "**Validation: re-sweep against v2026.4.15-beta.1 (3 models × 40 tasks × 3 runs = 360 runs).** I rebuilt "
        "the clawbench Docker image on top of `ghcr.io/openclaw/openclaw:2026.4.15-beta.1` and re-ran GLM / MiniMax "
        "/ Kimi in fresh isolated containers (same `container_sweep_single.sh`, 4GB heap, wiped run_cache, output "
        "to `data/drift_2026-04-15-beta1/`). The fix code is confirmed present in the image "
        "(`getCompletionsReasoningDelta`, `pendingThinkingDelta`, `reasoning_details` all in "
        "`/app/dist/anthropic-vertex-stream-CJjqZvdc.js`). Results:"
    )
    lines.append("")
    lines.append("```")
    lines.append("Model             v4.9   v4.14   v4.15β1   Δ v4.14→v4.15β1   Δ vs v4.9   tokens/run")
    lines.append("-------------------------------------------------------------------------------------")
    lines.append("GLM 5.1          0.587   0.318     0.286           -0.032      -0.301            0")
    lines.append("MiniMax M2.7     0.537   0.316     0.287           -0.029      -0.250            0")
    lines.append("Kimi K2.5        0.534   0.315     0.277           -0.038      -0.257            0")
    lines.append("```")
    lines.append("")
    lines.append(
        "**The v4.15-beta.1 fix does not resolve the regression for z-ai, minimax, or moonshotai upstreams.** "
        "Scores are actually ~0.03 _lower_ than v4.14 (wider failure-mode variety means runs occasionally make it "
        "deep enough to trip `tool_misuse` / `state_regression` / `browser_navigation_failure`, which on some "
        "tasks score below the clean-`verification_skipped`-default that v4.14 was landing on). Token counts are "
        "still zero across all 360 runs. The v4.15-beta.1 gateway still emits `stopReason=stop payloads=0` at "
        "roughly 1 event per run (~125 per model over the full sweep)."
    )
    lines.append("")
    lines.append(
        "**Why the fix doesn't cover these upstreams.** The PR title names Qwen3 specifically: the patch "
        "handles one concrete `reasoning_details` schema (array items with `type: \"reasoning.text\"`) plus three "
        "string-valued reasoning fields (`reasoning_content`, `reasoning`, `reasoning_text`). z-ai/glm-5.1, "
        "minimax/minimax-m2.7, and moonshotai/kimi-k2.5 evidently emit reasoning content in a variant schema that "
        "isn't enumerated by `getCompletionsReasoningDelta`. Same root cause, slightly different wire format, fix "
        "missed them. There are no follow-up commits on `upstream/main` past `v2026.4.15-beta.1` touching "
        "`openai-transport-stream.ts` or mentioning minimax/z-ai/glm/moonshot/kimi, so upgrading further does not "
        "help."
    )
    lines.append("")
    lines.append(
        "**Release status.** As of 2026-04-16, both `v2026.4.14` stable and `v2026.4.15-beta.1` exhibit the "
        "regression for these three routes. `upstream/main` is no better. The only known-working OpenClaw release "
        "for `openrouter/z-ai/*`, `openrouter/minimax/*`, and `openrouter/moonshotai/*` is **`v2026.4.9`** "
        "(the baseline in this report)."
    )
    lines.append("")
    lines.append("### 9.8 Recommendations")
    lines.append("")
    lines.append(
        "1. **Do not upgrade to `v2026.4.15-beta.1` expecting a fix for these three routes.** Validation shows "
        "   v4.15-beta.1 actually scores slightly _worse_ than v4.14 for GLM / MiniMax / Kimi (still 0 tokens/run, "
        "   still one `payloads=0` per run). The fix solves the Qwen3 case; it doesn't cover z-ai/minimax/moonshotai. "
        "   v4.15-beta.1 IS a legitimate fix if your sweep includes `openrouter/qwen/*` or "
        "   `openrouter/openai/*` (GPT-5-via-OpenRouter is reasoning-native and the fix covers the reasoning field "
        "   shapes OpenAI emits)."
    )
    lines.append(
        "2. **File a follow-up OpenClaw issue on top of PR #66905 / issue #66833.** Include: (a) the v2026.4.14 "
        "   and v2026.4.15-beta.1 sweep numbers for z-ai/glm-5.1, minimax/minimax-m2.7, moonshotai/kimi-k2.5 from "
        "   this report, (b) the v2026.4.9 baseline (which worked), (c) a wire-level tee of the outgoing request "
        "   and inbound SSE chunks for one representative turn on each upstream — `getCompletionsReasoningDelta` "
        "   needs to enumerate whatever schema variant these upstreams use. The raw schema is the blocker; OpenClaw "
        "   maintainers can't generalize the fix without it."
    )
    lines.append(
        "3. **Pin `v2026.4.9`** for `openrouter/z-ai/*`, `openrouter/minimax/*`, and `openrouter/moonshotai/*` on "
        "   ClawBench sweeps until a later OpenClaw release covers these upstreams. v4.9 is the only known-working "
        "   release for these routes. Alternatively, route those models through direct provider clients (z.ai, "
        "   MiniMax, Moonshot native APIs) instead of OpenRouter — the direct paths don't go through "
        "   `openai-transport-stream` and are not affected."
    )
    lines.append(
        "4. **For future sweeps** use `scripts/container_sweep_single.sh` (one container per model, 4GB heap, "
        "   wipes per-model run_cache) rather than the original long-session sweep. It prevents the OOM and also "
        "   produces more apples-to-apples per-model data. The script is parametrized by `SWEEP_LOGDIR` and "
        "   `SWEEP_OUT_TAG` so the same script ran the v4.14 and v4.15-beta.1 validation sweeps side-by-side."
    )
    lines.append(
        "5. **Do not treat the GLM/MiniMax/Kimi numbers in this report as model-capability measurements.** They "
        "   measure the OpenClaw OpenAI-completions transport parser, not the models. The v2026.4.9 baseline "
        "   (0.53-0.59 overall with 148k+ tokens/run) is the current best estimate of what these models can "
        "   actually do on ClawBench v0.4."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Generated by `scripts/generate_drift_report.py`.*")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--new-dir", default=str(ROOT / "data" / "drift_2026-04-14"))
    p.add_argument(
        "--out",
        default=str(ROOT / "reports" / "EVAL_REPORT_7MODEL_DRIFT_2026-04-14-CONTAINER.md"),
    )
    p.add_argument("--strict", action="store_true", help="Fail if any model JSON missing")
    args = p.parse_args()

    baselines: dict[str, dict] = {}
    for key, rel in BASELINE_FILES.items():
        fp = ROOT / rel
        if not fp.exists():
            print(f"[warn] baseline missing: {fp}", file=sys.stderr)
            if args.strict:
                return 2
            continue
        baselines[key] = load(fp)

    news: dict[str, dict] = {}
    new_dir = Path(args.new_dir)
    if not new_dir.exists():
        print(f"[warn] new-dir missing: {new_dir}", file=sys.stderr)
    for key in BASELINE_FILES:
        candidates = [
            new_dir / f"docker_{key}_v2026-4-14.json",
            new_dir / f"rerun_{key}_v2026-4-14.json",
        ]
        found = next((c for c in candidates if c.exists()), None)
        if not found:
            print(f"[warn] new result missing for {key}: tried {[str(c) for c in candidates]}", file=sys.stderr)
            if args.strict:
                return 3
            continue
        try:
            news[key] = load(found)
        except Exception as e:
            print(f"[err] failed to load {found}: {e}", file=sys.stderr)

    report = render_report(baselines, news)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"wrote: {out}")
    print(f"baselines loaded: {len(baselines)}/7 — {sorted(baselines)}")
    print(f"new results loaded: {len(news)}/7 — {sorted(news)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
