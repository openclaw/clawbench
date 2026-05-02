#!/usr/bin/env python3
"""Resume-capable supervisor for the repaired v4.9 latest-OpenClaw sweep."""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path


ROOT = Path(os.environ.get("ROOT", "/Users/zhentongfan/Desktop/openclaw/clawbench"))
IMAGE = os.environ.get("IMAGE", "clawbench-clawbench:v2026-4-26-agent-hotfix")
LOGDIR_CONT = os.environ.get("SWEEP_LOGDIR", "/data/drift_2026-04-28-v49-openclaw-426-hotfix")
OUT_TAG = os.environ.get("SWEEP_OUT_TAG", "v49-openclaw-426-hotfix")
RUNS = os.environ.get("SWEEP_RUNS", "3")
LANES = os.environ.get("SWEEP_LANES", "1")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "2"))
MEMORY = os.environ.get("DOCKER_MEMORY", "8g")
NAME_PREFIX = os.environ.get("NAME_PREFIX", "clawbench-v49-readyfix")
POLL_SECONDS = int(os.environ.get("SUPERVISOR_POLL_SECONDS", "60"))
INFRA_PATTERN = re.compile(
    r"no longer exists|env_unavailable|environment_unavailable|REJECTED|Traceback|"
    r"model_not_allowed|model not allowed|not allowed|WebSocket closed|API key|"
    r"billing|Insufficient|sessions.create.*✗|Gateway .*timed out|"
    r"control-plane.*timed out|connect.*timed out|RPC .*timed out|"
    r"agents.create timed out|sessions.create.*timed out"
)

TASKS = (
    "t3-debug-timezone-regression,t3-social-bill-split,t1-cal-quick-reminder,"
    "t4-cross-repo-migration,t5-contradictory-requirements,t3-node-multifile-refactor,"
    "t2-add-tests-normalizer,t3-cal-reschedule-cascade,t1-life-translate,"
    "t5-impossible-graceful-fail,t2-skill-excel-rollup,t1-fs-quick-note,"
    "t2-priv-redact-doc,t1-architecture-brief,t4-memory-recall-continuation,"
    "t2-fs-cleanup-downloads,t3-web-research-and-cite,t2-config-loader,"
    "t2-sys-memory-roundtrip,t4-ctx-long-recall,t3-fin-budget-monthly,"
    "t2-fs-find-that-thing,t3-data-pipeline-report,t2-ctx-pronoun-resolve,"
    "t5-hallucination-resistant-evidence,t2-msg-summarize-thread,t2-log-analyzer-cli,"
    "t2-node-search-patch,t4-delegation-repair,t3-data-sql-query,t2-web-quick-fact,"
    "t1-bugfix-discount,t4-life-trip-plan,t3-feature-export,t1-refactor-csv-loader,"
    "t3-monitoring-automation,t3-msg-inbox-triage,t2-browser-form-fix,"
    "t4-browser-research-and-code,t2-err-instruction-ambig"
)

RUNS_TO_START = [
    ("gpt55", "openai/gpt-5.5"),
    ("gpt54", "openai/gpt-5.4"),
    ("deepseekv4", "openrouter/deepseek/deepseek-v4-pro"),
    ("opus47", "anthropic/claude-opus-4-7"),
    ("opus46", "anthropic/claude-opus-4-6"),
    ("sonnet46", "anthropic/claude-sonnet-4-6"),
    ("minimax27", "openrouter/minimax/minimax-m2.7"),
    ("kimi26", "openrouter/moonshotai/kimi-k2.6"),
    ("glm51", "openrouter/z-ai/glm-5.1"),
    ("gemini31pro", "google/gemini-3.1-pro-preview"),
]


def host_logdir() -> Path:
    if LOGDIR_CONT.startswith("/data/"):
        return ROOT / "data" / LOGDIR_CONT.removeprefix("/data/")
    return ROOT / LOGDIR_CONT.lstrip("/")


LOGDIR_HOST = host_logdir()


def run(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=check)


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}", flush=True)


def safe_model(model: str) -> str:
    return model.replace("/", "_").replace(":", "_")


def result_path(label: str, model: str) -> Path:
    return LOGDIR_HOST / f"{label}_openclaw_{safe_model(model)}_{OUT_TAG}.json"


def container_name(label: str) -> str:
    return f"{NAME_PREFIX}-{label}"


def container_state(name: str) -> tuple[bool, str, int | None]:
    proc = run(
        [
            "docker",
            "inspect",
            name,
            "--format",
            "{{.State.Running}} {{.State.Status}} {{.State.ExitCode}}",
        ]
    )
    if proc.returncode != 0:
        return False, "missing", None
    parts = proc.stdout.strip().split()
    running = parts[0] == "true"
    status = parts[1] if len(parts) > 1 else "unknown"
    exit_code = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else None
    return running, status, exit_code


def launch(label: str, model: str) -> None:
    name = container_name(label)
    run(["docker", "rm", "-f", name])
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "-e",
        f"SWEEP_LABEL={label}",
        "-e",
        f"SWEEP_MODEL={model}",
        "-e",
        f"SWEEP_LOGDIR={LOGDIR_CONT}",
        "-e",
        f"SWEEP_OUT_TAG={OUT_TAG}",
        "-e",
        f"SWEEP_RUNS={RUNS}",
        "-e",
        f"SWEEP_LANES={LANES}",
        "-e",
        f"SWEEP_TASKS={TASKS}",
        "-e",
        "OPENCLAW_CONFIG_SOURCE=/config/openclaw",
        "-e",
        "OPENCLAW_EXEC_HOST=gateway",
        "-e",
        "CLAWBENCH_PER_RUN_BUDGET_SECONDS=900",
        "-e",
        "CLAWBENCH_PER_TURN_TIMEOUT_SECONDS=300",
        "-e",
        "CLAWBENCH_GATEWAY_READY_TIMEOUT_SECONDS=420",
        "-e",
        "CLAWBENCH_GATEWAY_PROBE_TIMEOUT_SECONDS=180",
        "-v",
        f"{ROOT / 'data'}:/data",
        "-v",
        f"{ROOT / 'data/container-home-openclaw'}:/config/openclaw:ro",
        "--memory",
        MEMORY,
        IMAGE,
        "bash",
        "/home/node/app/scripts/container_lane_eval.sh",
    ]
    proc = run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(
            f"failed to launch {label} with docker exit {proc.returncode}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    log(f"launched {label} {model} {proc.stdout.strip()}")


def ensure_started(label: str, model: str) -> None:
    path = result_path(label, model)
    if path.exists():
        log(f"skip {label}; result exists")
        return
    name = container_name(label)
    running, status, exit_code = container_state(name)
    if running:
        log(f"resume {label}; container already running")
        return
    if status != "missing":
        log(f"removing stale {label}; status={status} exit={exit_code}")
    launch(label, model)


def batch_done(batch: list[tuple[str, str]]) -> bool:
    all_done = True
    for label, model in batch:
        path = result_path(label, model)
        if path.exists():
            continue
        running, status, exit_code = container_state(container_name(label))
        if running:
            all_done = False
            continue
        log(f"{label} ended without result; status={status} exit={exit_code}")
    return all_done


def main() -> int:
    LOGDIR_HOST.mkdir(parents=True, exist_ok=True)
    log(f"supervisor start image={IMAGE} logdir={LOGDIR_HOST} runs={RUNS} lanes={LANES} batch={BATCH_SIZE}")
    failures = 0
    for index in range(0, len(RUNS_TO_START), BATCH_SIZE):
        batch = RUNS_TO_START[index : index + BATCH_SIZE]
        log(f"batch {index // BATCH_SIZE + 1} start: {', '.join(label for label, _ in batch)}")
        for label, model in batch:
            ensure_started(label, model)
        while not batch_done(batch):
            time.sleep(POLL_SECONDS)
        for label, model in batch:
            if result_path(label, model).exists():
                log(f"complete {label}")
            else:
                failures += 1
                log(f"failed {label}")
    infra_hits: list[str] = []
    for path in LOGDIR_HOST.glob("*"):
        if not path.is_file() or path.suffix not in {".log", ".json"}:
            continue
        try:
            for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if INFRA_PATTERN.search(line):
                    infra_hits.append(f"{path}:{line_no}: {line}")
                    if len(infra_hits) >= 80:
                        break
        except OSError:
            continue
        if len(infra_hits) >= 80:
            break
    if infra_hits:
        failures += 1
        log("infra gate found run-level signatures:")
        for hit in infra_hits:
            log(hit)
    else:
        log(f"infra gate clean: {LOGDIR_HOST}")
    log(f"supervisor done failures={failures}")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
