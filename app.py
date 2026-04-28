"""ClawBench HF Space — leaderboard + submission + background eval worker.

This single file is the entry point for the HF Docker Space.
It runs:
1. Gradio frontend (leaderboard + submission form + queue status)
2. Background eval worker (polls queue, runs benchmark, stores results)

All state persists via:
- /data/ directory (HF persistent storage)
- HF Dataset (`<space-owner>/clawbench-results`) for cross-restart persistence
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import gradio as gr
import pandas as pd
from clawbench.hub import (
    dataset_has_submission_results,
    load_submission_rows_from_parquet,
    resolve_dataset_repo,
)
from clawbench.queue import JobQueue, SubmissionRequest
from clawbench.submission_models import (
    build_preset_submission_specs,
    CUSTOM_PRESET_LABEL,
    PRESET_AUDIENCE_ALL,
    PRESET_AUDIENCE_CHOICES,
    PRESET_MODEL_MAP,
    preset_labels_for_audience,
    resolve_model_selection,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("clawbench.app")

RESULTS_DIR = Path("/data/results") if Path("/data").exists() else Path("data/results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
HF_DATASET_TOKEN = os.environ.get("HF_TOKEN", "")
HF_DATASET_REPO = resolve_dataset_repo(HF_DATASET_TOKEN)


@dataclass
class _LeaderboardCache:
    lock: threading.Lock = field(default_factory=threading.Lock)
    loaded_at: float = 0.0
    frame: pd.DataFrame | None = None


_LEADERBOARD_CACHE = _LeaderboardCache()


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, using default %s", name, raw, default)
        return default
    return max(minimum, min(maximum, value))


MAX_RUNS_PER_SUBMISSION = _env_int("CLAWBENCH_MAX_RUNS_PER_SUBMISSION", 3, minimum=1, maximum=10)
MAX_LANES_PER_SUBMISSION = _env_int("CLAWBENCH_MAX_LANES_PER_SUBMISSION", 4, minimum=1, maximum=8)
DEFAULT_RUNS_PER_TASK = _env_int("CLAWBENCH_DEFAULT_RUNS_PER_TASK", 3, minimum=1, maximum=MAX_RUNS_PER_SUBMISSION)
DEFAULT_PARALLEL_LANES = _env_int("CLAWBENCH_DEFAULT_PARALLEL_LANES", 1, minimum=1, maximum=MAX_LANES_PER_SUBMISSION)
LEADERBOARD_CACHE_SECONDS = _env_int("CLAWBENCH_LEADERBOARD_CACHE_SECONDS", 60, minimum=0, maximum=3600)
ENABLE_BULK_SUBMIT = os.environ.get("CLAWBENCH_ENABLE_BULK_SUBMIT", "").strip().lower() in {"1", "true", "yes", "on"}
JUDGE_AFFECTS_SCORE = os.environ.get("CLAWBENCH_JUDGE_AFFECTS_SCORE", "").strip().lower() in {"1", "true", "yes", "on"}

# ---------------------------------------------------------------------------
# Background worker (starts in a thread)
queue = JobQueue()


def _start_worker() -> None:
    """Start the eval worker in a background thread with its own event loop."""
    from clawbench.worker import EvalWorker

    async def _run():
        worker = EvalWorker(queue)
        await worker.start()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_run())


worker_thread = threading.Thread(target=_start_worker, daemon=True)
worker_thread.start()
logger.info("Background eval worker started")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_leaderboard() -> pd.DataFrame:
    now = time.monotonic()
    with _LEADERBOARD_CACHE.lock:
        if (
            _LEADERBOARD_CACHE.frame is not None
            and LEADERBOARD_CACHE_SECONDS > 0
            and now - _LEADERBOARD_CACHE.loaded_at < LEADERBOARD_CACHE_SECONDS
        ):
            return _LEADERBOARD_CACHE.frame.copy()

    frame = _load_leaderboard_uncached()
    if LEADERBOARD_CACHE_SECONDS > 0:
        with _LEADERBOARD_CACHE.lock:
            _LEADERBOARD_CACHE.loaded_at = time.monotonic()
            _LEADERBOARD_CACHE.frame = frame.copy()
    return frame.copy()


def _load_leaderboard_uncached() -> pd.DataFrame:
    rows = []

    # Load from HF Dataset via direct parquet reads. This avoids
    # `datasets.load_dataset(...)`, which triggers datasets-server metadata
    # requests that can intermittently fail even when the parquet shards are
    # perfectly readable.
    try:
        from huggingface_hub import HfApi

        api = HfApi(token=HF_DATASET_TOKEN or None)
        if dataset_has_submission_results(api, HF_DATASET_REPO):
            for row in load_submission_rows_from_parquet(
                HF_DATASET_REPO,
                token=HF_DATASET_TOKEN or None,
                api=api,
            ):
                rows.append(_flatten_result(row))
    except Exception as exc:
        logger.info("Remote leaderboard unavailable: %s", exc)

    # Load from local results
    if RESULTS_DIR.exists():
        for f in sorted(RESULTS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                rows.append(_flatten_result(data))
            except Exception:
                pass

    if not rows:
        return pd.DataFrame(columns=[
            "Model", "Judge Model", "Prompt", "Scenario", "Score", "Weighted", "Completion", "Trajectory",
            "Behavior", "Judge", "Reliability", "Hard", "Consensus", "Median ms", "Cost/Pass", "pass^k",
            "CI", "Tasks", "Timestamp",
        ])

    # Deduplicate by model + prompt variant + scenario + judge model (keep latest)
    seen = {}
    for r in rows:
        key = f"{r['Model']}|{r['Prompt']}|{r['Scenario']}|{r['Judge Model']}"
        if key not in seen or r["Timestamp"] > seen[key]["Timestamp"]:
            seen[key] = r

    df = pd.DataFrame(list(seen.values()))
    df = df.sort_values("Score", ascending=False).reset_index(drop=True)
    df.index = df.index + 1
    df.index.name = "#"
    return df


def _flatten_result(data: dict) -> dict:
    tasks = _parse_json_field(data.get("task_results", []), expected_type=list, default=[])
    n_tasks = len(tasks) if isinstance(tasks, list) else 0
    environment = _parse_json_field(data.get("environment", {}), expected_type=dict, default={})
    return {
        "Model": data.get("model", ""),
        "Judge Model": data.get("judge_model", environment.get("judge_model", "")) or "-",
        "Prompt": environment.get("prompt_variant", "clear"),
        "Scenario": environment.get("scenario", "all"),
        "Score": round(data.get("overall_score", data.get("overall_composite", 0)), 3),
        "Weighted": round(data.get("overall_weighted_query_score", 0), 3),
        "Completion": round(data.get("overall_completion", data.get("overall_state", 0)), 3),
        "Trajectory": round(data.get("overall_trajectory", 0), 3),
        "Behavior": round(data.get("overall_behavior", 0), 3),
        "Judge": round(data.get("overall_judge_score", 0), 3),
        "Reliability": round(data.get("overall_reliability", data.get("overall_pass_hat_k", 0)), 3),
        "Hard": round(data.get("hard_subset_score", 0), 3),
        "Consensus": round(data.get("consensus_subset_score", 0), 3),
        "Median ms": round(data.get("overall_median_latency_ms", 0)),
        "Cost/Pass": round(data.get("overall_cost_per_pass", 0), 4),
        "pass^k": f"{data.get('overall_pass_hat_k', 0):.0%}",
        "CI": f"{data.get('overall_ci_lower', 0):.2f}-{data.get('overall_ci_upper', 0):.2f}",
        "Tasks": n_tasks,
        "Timestamp": data.get("timestamp", "")[:16],
    }


def _parse_json_field(value, *, expected_type, default):
    if isinstance(value, expected_type):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            try:
                import ast
                parsed = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return default
        return parsed if isinstance(parsed, expected_type) else default
    return default


def load_queue() -> pd.DataFrame:
    jobs = asyncio.run(queue.list_jobs(limit=20))
    if not jobs:
        return pd.DataFrame(
            columns=[
                "ID",
                "Model",
                "Judge",
                "Prompt",
                "Scenario",
                "Status",
                "Submitted",
                "Started",
                "Heartbeat",
                "Task",
                "Run",
                "Runs",
                "Lanes",
                "Attempts",
                "Requeues",
                "Progress",
            ]
        )
    rows = []
    for j in jobs:
        run_label = ""
        if j.current_run_index is not None and j.current_run_total is not None:
            run_label = f"{j.current_run_index}/{j.current_run_total}"
        rows.append({
            "ID": j.job_id,
            "Model": j.request.model,
            "Judge": j.request.judge_model or "-",
            "Prompt": j.request.prompt_variant,
            "Scenario": j.request.scenario or "all",
            "Status": j.status.value,
            "Submitted": j.submitted_at[:16] if j.submitted_at else "",
            "Started": j.started_at[:16] if j.started_at else "",
            "Heartbeat": j.last_progress_at[:16] if j.last_progress_at else "",
            "Task": j.current_task_id or "-",
            "Run": run_label or "-",
            "Runs": j.request.runs_per_task,
            "Lanes": j.request.max_parallel_lanes,
            "Attempts": j.attempt_count,
            "Requeues": j.stale_requeues,
            "Progress": j.progress_message or "-",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Submission handler
# ---------------------------------------------------------------------------


def submit_model(
    model: str,
    preset: str,
    provider: str,
    judge_model: str,
    runs: int,
    max_parallel_lanes: int,
    tier: str | None,
    scenario: str | None,
    prompt_variant: str,
    submitter: str,
) -> str:
    model_id, provider_id = resolve_model_selection(model, preset, provider)
    if not model_id:
        return "Please enter a model ID or select a preset."

    selected_tier = tier if tier != "all" else None
    request = SubmissionRequest(
        model=model_id,
        provider=provider_id,
        judge_model=judge_model.strip(),
        judge_affects_score=JUDGE_AFFECTS_SCORE,
        runs_per_task=int(runs),
        max_parallel_lanes=int(max_parallel_lanes),
        tier=selected_tier,
        scenario=scenario if scenario != "all" else None,
        prompt_variant=prompt_variant,
        submitter=submitter.strip(),
    )
    try:
        job = asyncio.run(queue.submit(request))
    except ValueError as exc:
        return f"Submission blocked: {exc}"
    return f"Queued [{model_id}]. Job ID: {job.job_id}. Check the Queue tab."


def submit_all_presets(
    preset_audience: str,
    runs: int,
    max_parallel_lanes: int,
    judge_model: str,
    tier: str | None,
    scenario: str | None,
    prompt_variant: str,
    submitter: str,
) -> str:
    """Submit all preset models from the selected audience track."""
    if not ENABLE_BULK_SUBMIT:
        return (
            "Bulk preset submission is disabled for this deployment. "
            "Set CLAWBENCH_ENABLE_BULK_SUBMIT=1 to enable it for maintainer runs."
        )

    selected_tier = tier if tier != "all" else None
    selected_scenario = scenario if scenario != "all" else None
    preset_specs = build_preset_submission_specs(
        preset_audience,
        runs=int(runs),
        max_parallel_lanes=int(max_parallel_lanes),
        judge_model=judge_model,
        tier=selected_tier,
        scenario=selected_scenario,
        prompt_variant=prompt_variant,
        submitter=submitter,
    )
    if not preset_specs:
        return f"No presets configured for {preset_audience}."

    submitted = []
    blocked = []
    for preset, request_kwargs in preset_specs:
        request_kwargs["judge_affects_score"] = JUDGE_AFFECTS_SCORE
        request = SubmissionRequest(**request_kwargs)
        try:
            job = asyncio.run(queue.submit(request))
        except ValueError as exc:
            blocked.append(f"{preset.label}: {exc}")
            continue
        submitted.append(f"{preset.label} ({job.job_id})")
    message = f"Queued {len(submitted)} models from {preset_audience}:\n" + "\n".join(
        f"  - {item}" for item in submitted
    )
    if blocked:
        message += "\n\nBlocked:\n" + "\n".join(f"  - {item}" for item in blocked)
    return message


def update_preset_choices(preset_audience: str):
    return gr.update(
        choices=[CUSTOM_PRESET_LABEL] + preset_labels_for_audience(preset_audience),
        value=CUSTOM_PRESET_LABEL,
    )


# ---------------------------------------------------------------------------
# Theme + CSS — matched to OpenClaw WebUI & ClawHub design system
# ---------------------------------------------------------------------------

CUSTOM_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
    /* Background — deep, rich dark with layered depth (from WebUI base.css) */
    --bg: #0e1015;
    --bg-accent: #13151b;
    --bg-elevated: #191c24;
    --bg-hover: #1f2330;

    /* Card / Surface */
    --card: #161920;
    --card-foreground: #f0f0f2;

    /* Text — clean contrast */
    --text: #d4d4d8;
    --text-strong: #f4f4f5;
    --muted: #838387;
    --muted-strong: #75757d;

    /* Border — whisper-thin, barely there */
    --border: #1e2028;
    --border-strong: #2e3040;
    --border-hover: #3e4050;

    /* Accent — punchy signature red (from WebUI) */
    --accent: #ff5c5c;
    --accent-hover: #ff7070;
    --accent-subtle: rgba(255, 92, 92, 0.1);
    --accent-glow: rgba(255, 92, 92, 0.2);

    /* Secondary accent — teal (from WebUI) */
    --accent-2: #14b8a6;
    --accent-2-subtle: rgba(20, 184, 166, 0.1);

    /* Semantic */
    --ok: #22c55e;
    --ok-subtle: rgba(34, 197, 94, 0.08);
    --warn: #f59e0b;
    --warn-subtle: rgba(245, 158, 11, 0.08);

    /* Typography */
    --font-body: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;

    /* Shadows — subtle, layered depth */
    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.25);
    --shadow-md: 0 4px 16px rgba(0, 0, 0, 0.3);
    --shadow-lg: 0 12px 32px rgba(0, 0, 0, 0.4);

    /* Radii */
    --radius-sm: 6px;
    --radius-md: 10px;
    --radius-lg: 14px;

    /* Transitions */
    --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
    --duration-fast: 100ms;
    --duration-normal: 180ms;
}

/* ── Global ──────────────────────────────────────────── */

.gradio-container {
    background: var(--bg) !important;
    font-family: var(--font-body) !important;
    max-width: 1200px !important;
    margin: 0 auto !important;
    color: var(--text) !important;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}

.main, .contain {
    background: var(--bg) !important;
}

footer { display: none !important; }

/* ── Hero ────────────────────────────────────────────── */

#hero-block {
    background: var(--bg-accent) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-lg) !important;
    padding: 36px 40px 32px !important;
    margin-bottom: 6px !important;
    position: relative;
    overflow: hidden;
}

#hero-block::before {
    content: '';
    position: absolute;
    top: -40%;
    left: -15%;
    width: 55%;
    height: 180%;
    background: radial-gradient(ellipse, rgba(255,92,92,0.04) 0%, transparent 65%);
    pointer-events: none;
}

#hero-block h1 {
    font-family: var(--font-body) !important;
    font-size: 1.9rem !important;
    font-weight: 700 !important;
    letter-spacing: -0.03em !important;
    color: var(--text-strong) !important;
    margin: 0 0 8px 0 !important;
    line-height: 1.15 !important;
}

#hero-block .accent { color: var(--accent) !important; }

#hero-block p {
    color: var(--muted) !important;
    font-size: 0.9rem !important;
    line-height: 1.6 !important;
    margin: 0 !important;
}

#hero-block p a {
    color: var(--accent) !important;
    text-decoration: underline !important;
    text-underline-offset: 2px !important;
    text-decoration-color: rgba(255,92,92,0.35) !important;
}

#hero-block p a:hover {
    text-decoration-thickness: 2px !important;
}

#hero-block p strong {
    color: var(--text) !important;
    font-weight: 600 !important;
}

/* ── Stat pills ──────────────────────────────────────── */

#stat-row {
    padding: 0 !important;
    margin-bottom: 10px !important;
    gap: 8px !important;
    background: transparent !important;
    border: none !important;
}

#stat-row > div {
    background: transparent !important;
    border: none !important;
}

.stat-pill {
    background: var(--card) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    padding: 12px 16px !important;
}

.stat-pill .label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    font-weight: 600;
    color: var(--muted);
    font-family: var(--font-body);
}

.stat-pill .value {
    font-family: var(--font-mono);
    font-size: 1.2rem;
    font-weight: 600;
    color: var(--text-strong);
    margin-top: 2px;
}

.stat-pill .value.accent { color: var(--accent); }
.stat-pill .value.teal { color: var(--accent-2); }

/* ── Tabs — pill switcher (matches WebUI topbar-theme-mode pattern) ── */

.tabs {
    background: transparent !important;
    border: none !important;
}

.tab-nav {
    background: color-mix(in srgb, var(--bg-elevated) 88%, transparent) !important;
    border: 1px solid color-mix(in srgb, var(--border) 84%, transparent) !important;
    border-radius: 9999px !important;
    padding: 3px !important;
    gap: 2px !important;
    margin-bottom: 14px !important;
    display: inline-flex !important;
}

.tab-nav button {
    font-family: var(--font-body) !important;
    font-weight: 600 !important;
    font-size: 0.82rem !important;
    color: var(--muted) !important;
    background: transparent !important;
    border: 1px solid transparent !important;
    border-radius: 9999px !important;
    padding: 8px 18px !important;
    transition: color var(--duration-fast) ease,
                background var(--duration-fast) ease,
                border-color var(--duration-fast) ease !important;
}

.tab-nav button:hover {
    color: var(--text) !important;
    background: var(--bg-hover) !important;
}

.tab-nav button.selected {
    color: var(--text-strong) !important;
    background: color-mix(in srgb, var(--accent-subtle) 88%, var(--bg-elevated) 12%) !important;
    border-color: color-mix(in srgb, var(--accent) 18%, transparent) !important;
    box-shadow: inset 0 1px 0 color-mix(in srgb, white 10%, transparent) !important;
}

.tabitem {
    background: transparent !important;
    border: none !important;
    padding: 0 !important;
}

/* ── Dataframe / table ───────────────────────────────── */

.dataframe, .table-wrap, .svelte-1gfkn6j {
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    overflow: hidden !important;
    background: var(--card) !important;
}

table {
    font-family: var(--font-mono) !important;
    font-size: 0.78rem !important;
    border-collapse: separate !important;
    border-spacing: 0 !important;
    width: 100% !important;
}

table thead tr {
    background: var(--bg-elevated) !important;
}

table thead th {
    color: color-mix(in srgb, var(--muted) 72%, var(--text) 28%) !important;
    font-weight: 700 !important;
    font-size: 0.65rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.06em !important;
    padding: 11px 12px !important;
    border-bottom: 1px solid var(--border) !important;
    white-space: nowrap !important;
    background: var(--bg-elevated) !important;
}

table tbody tr {
    transition: background var(--duration-fast) ease !important;
}

table tbody tr:hover {
    background: color-mix(in srgb, var(--bg-hover) 84%, transparent) !important;
}

table tbody td {
    color: var(--text) !important;
    padding: 9px 12px !important;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 60%, transparent) !important;
    white-space: nowrap !important;
}

table tbody tr:last-child td {
    border-bottom: none !important;
}

/* ── Buttons (matches WebUI .btn pattern) ────────────── */

.primary.svelte-cmf5ev, button.primary {
    background: var(--accent) !important;
    border: 1px solid color-mix(in srgb, var(--accent) 60%, transparent) !important;
    color: #fff !important;
    font-family: var(--font-body) !important;
    font-weight: 600 !important;
    font-size: 0.85rem !important;
    border-radius: var(--radius-md) !important;
    padding: 9px 20px !important;
    transition: background var(--duration-fast) ease,
                border-color var(--duration-fast) ease,
                transform var(--duration-fast) ease !important;
    box-shadow: var(--shadow-sm) !important;
}

.primary.svelte-cmf5ev:hover, button.primary:hover {
    background: var(--accent-hover) !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 0 24px var(--accent-glow) !important;
}

.secondary.svelte-cmf5ev, button.secondary {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    color: var(--muted) !important;
    font-family: var(--font-body) !important;
    font-weight: 500 !important;
    font-size: 0.85rem !important;
    border-radius: var(--radius-md) !important;
    padding: 9px 20px !important;
    transition: background var(--duration-fast) ease,
                border-color var(--duration-fast) ease,
                color var(--duration-fast) ease !important;
}

.secondary.svelte-cmf5ev:hover, button.secondary:hover {
    border-color: var(--border-strong) !important;
    color: var(--text) !important;
    background: var(--bg-hover) !important;
}

button[variant="stop"] {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
}

.refresh-btn {
    max-width: 110px !important;
}

/* ── Inputs (matches WebUI input pattern) ────────────── */

.block, .form {
    background: transparent !important;
    border: none !important;
}

input[type="text"], textarea, .wrap.svelte-1gfkn6j {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    color: var(--text) !important;
    font-family: var(--font-body) !important;
    font-size: 0.88rem !important;
    padding: 9px 12px !important;
    transition: border-color var(--duration-fast) ease !important;
}

input[type="text"]:focus, textarea:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 2px var(--bg), 0 0 0 3px color-mix(in srgb, var(--accent) 40%, transparent) !important;
    outline: none !important;
}

label, .label-wrap span {
    color: var(--muted) !important;
    font-family: var(--font-body) !important;
    font-weight: 500 !important;
    font-size: 0.82rem !important;
}

/* Dropdown */
.wrap.svelte-1gfkn6j, .secondary-wrap {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
}

/* Slider */
input[type="range"] {
    accent-color: var(--accent) !important;
}

/* ── Markdown ────────────────────────────────────────── */

.prose, .markdown-text, .md {
    color: var(--text) !important;
    font-family: var(--font-body) !important;
}

.prose h2, .prose h3, .md h2, .md h3 {
    font-family: var(--font-body) !important;
    color: var(--text-strong) !important;
    font-weight: 650 !important;
    letter-spacing: -0.02em !important;
}

.prose h2, .md h2 {
    font-size: 1.25rem !important;
    padding-bottom: 10px !important;
    border-bottom: 1px solid var(--border) !important;
    margin-bottom: 16px !important;
}

.prose h3, .md h3 {
    font-size: 1rem !important;
    color: var(--accent) !important;
    margin-top: 24px !important;
}

.prose code, .md code {
    font-family: var(--font-mono) !important;
    background: var(--bg-elevated) !important;
    color: var(--accent) !important;
    padding: 2px 6px !important;
    border-radius: var(--radius-sm) !important;
    font-size: 0.8rem !important;
    border: 1px solid var(--border) !important;
}

.prose pre, .md pre {
    background: var(--bg-accent) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    padding: 16px 20px !important;
    overflow-x: auto;
}

.prose pre code, .md pre code {
    background: transparent !important;
    border: none !important;
    padding: 0 !important;
    color: var(--muted) !important;
    font-size: 0.78rem !important;
    line-height: 1.65 !important;
}

/* Markdown tables */
.prose table, .md table {
    border-collapse: collapse !important;
    margin: 14px 0 !important;
    font-family: var(--font-mono) !important;
    font-size: 0.78rem !important;
}

.prose table th, .md table th {
    background: var(--bg-elevated) !important;
    color: color-mix(in srgb, var(--muted) 72%, var(--text) 28%) !important;
    font-weight: 700 !important;
    text-transform: uppercase !important;
    font-size: 0.65rem !important;
    letter-spacing: 0.06em !important;
    padding: 9px 14px !important;
    border: 1px solid var(--border) !important;
}

.prose table td, .md table td {
    padding: 8px 14px !important;
    border: 1px solid var(--border) !important;
    color: var(--text) !important;
}

.prose table tr:hover td, .md table tr:hover td {
    background: color-mix(in srgb, var(--bg-hover) 60%, transparent) !important;
}

.prose strong, .md strong {
    color: var(--text-strong) !important;
    font-weight: 600 !important;
}

.prose a, .md a {
    color: var(--accent) !important;
    text-decoration: underline !important;
    text-underline-offset: 2px !important;
    text-decoration-color: rgba(255,92,92,0.35) !important;
}

.prose a:hover, .md a:hover {
    text-decoration-thickness: 2px !important;
}

.prose ul, .md ul {
    color: var(--text) !important;
}

.prose li, .md li {
    line-height: 1.65 !important;
}

/* ── Status output ───────────────────────────────────── */

.output-textbox textarea {
    background: var(--bg-accent) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    color: var(--ok) !important;
    font-family: var(--font-mono) !important;
    font-size: 0.8rem !important;
}

/* ── Scrollbar (from WebUI base.css) ─────────────────── */

::-webkit-scrollbar {
    width: 6px;
    height: 6px;
}

::-webkit-scrollbar-track {
    background: transparent;
}

::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.08);
    border-radius: 9999px;
}

::-webkit-scrollbar-thumb:hover {
    background: rgba(255, 255, 255, 0.14);
}

/* ── Animations (from WebUI) ─────────────────────────── */

@keyframes rise {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
}

#hero-block { animation: rise 0.3s var(--ease-out); }
#stat-row { animation: rise 0.3s var(--ease-out) 0.05s both; }
.tabs { animation: rise 0.3s var(--ease-out) 0.1s both; }
"""

def _build_theme():
    """Build the custom theme, falling back to plain Base() on any error.

    Different Gradio versions accept different ``.set()`` kwargs; we want
    any unknown kwarg to degrade gracefully instead of killing the Space.
    """
    try:
        return gr.themes.Base(
            primary_hue=gr.themes.colors.red,
            secondary_hue=gr.themes.colors.gray,
            neutral_hue=gr.themes.colors.gray,
            font=gr.themes.GoogleFont("Inter"),
            font_mono=gr.themes.GoogleFont("JetBrains Mono"),
        ).set(
            body_background_fill="#0e1015",
            body_background_fill_dark="#0e1015",
            body_text_color="#d4d4d8",
            body_text_color_dark="#d4d4d8",
            body_text_color_subdued="#838387",
            body_text_color_subdued_dark="#838387",
            background_fill_primary="#13151b",
            background_fill_primary_dark="#13151b",
            background_fill_secondary="#191c24",
            background_fill_secondary_dark="#191c24",
            border_color_primary="#1e2028",
            border_color_primary_dark="#1e2028",
            block_background_fill="#161920",
            block_background_fill_dark="#161920",
            block_border_color="#1e2028",
            block_border_color_dark="#1e2028",
            block_label_background_fill="#191c24",
            block_label_background_fill_dark="#191c24",
            block_label_text_color="#838387",
            block_label_text_color_dark="#838387",
            block_title_text_color="#d4d4d8",
            block_title_text_color_dark="#d4d4d8",
            input_background_fill="#191c24",
            input_background_fill_dark="#191c24",
            input_border_color="#1e2028",
            input_border_color_dark="#1e2028",
            input_border_color_focus="#ff5c5c",
            input_border_color_focus_dark="#ff5c5c",
            button_primary_background_fill="#ff5c5c",
            button_primary_background_fill_dark="#ff5c5c",
            button_primary_border_color="#ff5c5c",
            button_primary_border_color_dark="#ff5c5c",
            button_primary_text_color="#ffffff",
            button_primary_text_color_dark="#ffffff",
            button_secondary_background_fill="#191c24",
            button_secondary_background_fill_dark="#191c24",
            button_secondary_border_color="#1e2028",
            button_secondary_border_color_dark="#1e2028",
            button_secondary_text_color="#838387",
            button_secondary_text_color_dark="#838387",
        )
    except Exception as exc:
        logger.warning("Custom theme failed (%s); falling back to Base()", exc)
        return gr.themes.Base()


clawbench_theme = _build_theme()


# ---------------------------------------------------------------------------
# Gradio app
# ---------------------------------------------------------------------------

HERO_HTML = """
<h1>Claw<span class="accent">Bench</span></h1>
<p>
Rigorous benchmark for AI models as <a href="https://github.com/openclaw/openclaw" target="_blank">OpenClaw</a> agents.
Submit a model and it will be evaluated on HF infrastructure.<br>
<strong>Axes</strong>: Completion &middot; Trajectory &middot; Behavior &middot; Reliability
&nbsp;&nbsp;|&nbsp;&nbsp;
<strong>Primary</strong>: pass^k
</p>
"""

# ── Stat counts: computed dynamically from the live task corpus so the
#    ribbon tracks additions/removals without manual edits. ──
def _compute_stats() -> dict[str, int]:
    try:
        from clawbench.tasks import load_all_tasks
        tasks = load_all_tasks()
    except Exception as exc:
        logger.warning("Stat computation failed, falling back to defaults: %s", exc)
        return {"tasks": 40, "tiers": 5, "browser": 2, "judge": 6}
    n_tasks = len(tasks)
    n_tiers = len({t.tier.value for t in tasks}) or 5
    n_browser = sum(1 for t in tasks if t.family.value == "browser")
    n_judge = sum(1 for t in tasks if t.judge is not None)
    return {"tasks": n_tasks, "tiers": n_tiers, "browser": n_browser, "judge": n_judge}


_STATS = _compute_stats()

STAT_TASKS = (
    f'<div class="stat-pill"><div class="label">Tasks</div>'
    f'<div class="value">{_STATS["tasks"]}</div></div>'
)
STAT_TIERS = (
    f'<div class="stat-pill"><div class="label">Tiers</div>'
    f'<div class="value">{_STATS["tiers"]}</div></div>'
)
STAT_BROWSER = (
    f'<div class="stat-pill"><div class="label">Browser</div>'
    f'<div class="value accent">{_STATS["browser"]}</div></div>'
)
STAT_JUDGE = (
    f'<div class="stat-pill"><div class="label">Judge</div>'
    f'<div class="value accent">{_STATS["judge"]}</div></div>'
)
STAT_PRESETS = (
    '<div class="stat-pill"><div class="label">Presets</div><div class="value teal">'
    + str(len(PRESET_MODEL_MAP))
    + "</div></div>"
)

with gr.Blocks(title="ClawBench", theme=clawbench_theme, css=CUSTOM_CSS) as demo:

    # ── Hero ──
    gr.HTML(HERO_HTML, elem_id="hero-block")

    # ── Stats ribbon ──
    with gr.Row(elem_id="stat-row", equal_height=True):
        gr.HTML(STAT_TASKS)
        gr.HTML(STAT_TIERS)
        gr.HTML(STAT_BROWSER)
        gr.HTML(STAT_JUDGE)
        gr.HTML(STAT_PRESETS)

    # ── Tabs ──
    with gr.Tab("Leaderboard"):
        refresh_btn = gr.Button("Refresh", scale=0, elem_classes=["refresh-btn"])
        leaderboard = gr.Dataframe(
            value=load_leaderboard,
            interactive=False,
            wrap=True,
        )
        refresh_btn.click(fn=load_leaderboard, outputs=leaderboard)

    with gr.Tab("Submit"):
        gr.Markdown("### Submit a model for evaluation")
        gr.Markdown(
            "Select a preset or enter a custom model ID. Open-source models "
            "run via HuggingFace Inference API. You can also use locally hosted models "
            "(for example Ollama) when your OpenClaw runtime has them configured."
        )
        gr.Markdown(
            "Use `Preset Audience` to switch between the full Claw catalog and a smaller budget track. "
            "The budget track keeps local and lower-cost options upfront, including `ollama/gpt-oss:20b`, "
            "`ollama/qwen3.5:27b`, `huggingface/Qwen/Qwen3-32B`, and "
            "`huggingface/google/gemma-4-26B-A4B-it`."
        )

        preset_audience_input = gr.Dropdown(
            choices=list(PRESET_AUDIENCE_CHOICES),
            value=PRESET_AUDIENCE_ALL,
            label="Preset Audience",
        )
        preset_input = gr.Dropdown(
            choices=[CUSTOM_PRESET_LABEL] + preset_labels_for_audience(PRESET_AUDIENCE_ALL),
            value=CUSTOM_PRESET_LABEL,
            label="Preset models",
        )
        preset_audience_input.change(
            fn=update_preset_choices,
            inputs=preset_audience_input,
            outputs=preset_input,
        )
        with gr.Row():
            model_input = gr.Textbox(
                label="Custom Model ID (if not using preset)",
                placeholder="e.g. huggingface/org/model-name or ollama/gpt-oss:20b",
                scale=3,
            )
            provider_input = gr.Textbox(
                label="Provider",
                placeholder="auto-detected from model ID",
                scale=1,
            )
        judge_model_input = gr.Textbox(
            label="Judge Model (optional, advisory only)",
            placeholder="e.g. anthropic/claude-opus-4-6",
        )
        with gr.Row():
            runs_input = gr.Slider(
                minimum=1, maximum=MAX_RUNS_PER_SUBMISSION, value=DEFAULT_RUNS_PER_TASK, step=1,
                label="Runs per task (higher = more reliable pass^k)",
            )
            max_parallel_lanes_input = gr.Slider(
                minimum=1,
                maximum=MAX_LANES_PER_SUBMISSION,
                value=DEFAULT_PARALLEL_LANES,
                step=1,
                label="Parallel lanes (browser tasks stay serialized on one lane)",
            )
            tier_input = gr.Dropdown(
                choices=["all", "tier1", "tier2", "tier3", "tier4", "tier5"],
                value="all",
                label="Tier",
            )
        gr.Markdown(
            "Use more than 1 lane only on CPU-upgraded Spaces. Non-browser tasks can run in parallel, "
            "while browser tasks stay on a dedicated serial lane to avoid port and Chromium contention."
        )
        with gr.Row():
            scenario_input = gr.Dropdown(
                choices=[
                    "all",
                    "coding_dev_assist",
                    "data_processing_analysis",
                    "web_info_ops",
                    "multi_step_compound",
                    "context_continuation",
                    "error_boundary_cases",
                    "system_capabilities",
                ],
                value="all",
                label="Scenario",
            )
            prompt_variant_input = gr.Dropdown(
                choices=["clear", "ambiguous"],
                value="clear",
                label="Prompt Variant",
            )
        submitter_input = gr.Textbox(
            label="Your name (optional)",
            placeholder="HF username",
        )
        with gr.Row():
            submit_btn = gr.Button("Submit Model", variant="primary")
            submit_all_btn = gr.Button("Submit All Presets", variant="secondary", interactive=ENABLE_BULK_SUBMIT)
        submit_output = gr.Textbox(label="Status", interactive=False, lines=5, elem_classes=["output-textbox"])
        submit_btn.click(
            fn=submit_model,
            inputs=[
                model_input,
                preset_input,
                provider_input,
                judge_model_input,
                runs_input,
                max_parallel_lanes_input,
                tier_input,
                scenario_input,
                prompt_variant_input,
                submitter_input,
            ],
            outputs=submit_output,
        )
        submit_all_btn.click(
            fn=submit_all_presets,
            inputs=[
                preset_audience_input,
                runs_input,
                max_parallel_lanes_input,
                judge_model_input,
                tier_input,
                scenario_input,
                prompt_variant_input,
                submitter_input,
            ],
            outputs=submit_output,
        )

        gr.Markdown("""
**Preset audiences:**

| Audience | What it optimizes for | Presets |
|---|---|---|
| Claw Users | Full preset catalog, including provider-backed frontier options | Anthropic, HF open-weight, and Ollama presets |
| Budget Researchers | Smaller local/free-friendly track | GPT-OSS 20B, Qwen 3.5 27B, Qwen3 32B, Gemma 4 26B |

**Current preset catalog:**

| Model | Provider | Audience |
|---|---|---|
| GPT-OSS 20B (Ollama) | Ollama | Claw Users, Budget Researchers |
| Qwen 3.5 27B (Ollama) | Ollama | Claw Users, Budget Researchers |
| Qwen3 32B | HuggingFace | Claw Users, Budget Researchers |
| Gemma 4 26B MoE | HuggingFace | Claw Users, Budget Researchers |
| GLM 5.1 | HuggingFace | Claw Users |
| GLM 5 | HuggingFace | Claw Users |
| DeepSeek R1 | HuggingFace | Claw Users |
| Kimi K2 Instruct | HuggingFace | Claw Users |
| MiniMax M2.5 | HuggingFace | Claw Users |
| Llama 3.3 70B | HuggingFace | Claw Users |
| Llama 3.1 70B | HuggingFace | Claw Users |
| Claude Sonnet 4.6 | Anthropic | Claw Users |
| Claude Opus 4.6 | Anthropic | Claw Users |
""")

    with gr.Tab("Queue"):
        gr.Markdown("### Evaluation Queue")
        gr.Markdown(
            "Heartbeat and progress fields update during long evaluations. "
            "If a job loses its lease after a restart, the worker will auto-requeue it."
        )
        queue_refresh = gr.Button("Refresh", scale=0, elem_classes=["refresh-btn"])
        queue_table = gr.Dataframe(value=load_queue, interactive=False, wrap=True)
        queue_refresh.click(fn=load_queue, outputs=queue_table)

    with gr.Tab("Methodology"):
        gr.Markdown("""
## How ClawBench evaluates agents

### Design principles
- verify the actual work instead of trusting the transcript
- score trajectory properties instead of one reference trace
- keep the official score deterministic
- reward reliability across repeated runs
- expose coverage through tier, scenario, prompt, and subset slices

### Runtime flow
```text
task yaml + assets
  -> isolated workspace
  -> optional local background services
  -> OpenClaw agent session(s)
  -> transcript + tool-result correlation
  -> completion / trajectory / behavior scoring
  -> repeated runs
  -> reliability aggregation
```

### Completion
After the agent runs, we execute tests, scripts, and deterministic checks against the actual workspace and gateway state.
We **never** trust what the agent said. We verify the work by running it.

### Trajectory
We score trajectory properties instead of matching a reference trace:
- exploration before mutation
- recovery quality after failures
- tool-family fit for the task
- safety and forbidden operations

### Behavior
Behavior is deterministic and transcript-based:
- planning when the task calls for it
- progress communication on longer tasks
- graceful blocker or refusal language
- no destructive commands

### Reliability
Per-run score uses completion, trajectory, and behavior.
Per-task leaderboard score then adds a reliability term across all runs using `pass^k`, pass rate, and variance.

Current formula:
- normalized weighted average of `0.4 completion + 0.3 trajectory + 0.2 behavior`
- `0.9 * mean_run_score + 0.1 * reliability_score`

### Query Benchmark Layer
- scenario coverage mapped from a 12-domain user-query dataset
- `clear` and `ambiguous` prompt variants for robustness slices
- pass / partial / fail delivery buckets as a user-facing outcome view
- weighted query-score reporting for a second aggregate view

### Advisory LLM Judge
- optional task-level rubric checks for nuanced quality on selected high-ambiguity tasks
- artifact-aware judging prompts using the produced files plus transcript excerpts
- reported as a sidecar signal and does not change the official deterministic leaderboard score

### Task Design
- 19 tasks across 5 tiers
- deterministic local services for browser tasks
- multi-file assets with real bugs, missing tests, and migration work
- scripted user turns and optional multi-phase fresh-session tasks

### Coverage snapshot
```text
Tier mix
tier1 | ##     2
tier2 | ###### 6
tier3 | #####  5
tier4 | #####  5
tier5 | #      1

Family mix
tools       | ######## 8
repo        | ###      3
coding      | ##       2
multi_tool  | ###      3
browser     | ##       2
adversarial | #        1
```

### pass^k: Production Reliability
| pass@1 | pass^5 | pass^8 |
|--------|--------|--------|
| 90% | 59% | 43% |
| 95% | 77% | 66% |
| 99% | 95% | 92% |

### Based on
- [TAU-bench](https://github.com/sierra-research/tau-bench) — POMDP, pass^k, state verification
- [SWE-bench](https://www.swebench.com/) — deterministic test-based verification
- [WebArena](https://webarena.dev/) — programmatic state assertions
- [Anthropic eval guide](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
""")


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
