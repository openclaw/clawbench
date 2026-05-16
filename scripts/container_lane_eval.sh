#!/bin/bash
# Run one OpenClaw model/profile through the HF-style isolated lane worker.
set -Eeuo pipefail

: "${SWEEP_MODEL:?SWEEP_MODEL required}"
: "${SWEEP_LABEL:?SWEEP_LABEL required}"
: "${SWEEP_OUT_TAG:=lane-container}"
: "${SWEEP_LANES:=3}"
: "${SWEEP_RUNS:=1}"
: "${SWEEP_LOGDIR:=/data/results}"
: "${CLAWBENCH_PER_RUN_BUDGET_SECONDS:=900}"
: "${CLAWBENCH_PER_TURN_TIMEOUT_SECONDS:=300}"
: "${OPENCLAW_EXEC_HOST:=gateway}"

cd /home/node/app
export CLAWBENCH_LOCAL_QUEUE_DIR="${CLAWBENCH_LOCAL_QUEUE_DIR:-/data/queue/$SWEEP_LABEL}"
export CLAWBENCH_CODEX_DYNAMIC_TOOLS_LOADING="${CLAWBENCH_CODEX_DYNAMIC_TOOLS_LOADING:-searchable}"
mkdir -p "$SWEEP_LOGDIR" /data/results "$CLAWBENCH_LOCAL_QUEUE_DIR" /data/run_cache /data/lane_runtime

export HF_TOKEN=""
export OPENCLAW_GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-local-dev-token-for-testing}"
export OPENCLAW_SKIP_GMAIL_WATCHER=1
export OPENCLAW_SKIP_CANVAS_HOST=1
export OPENCLAW_SKIP_CHANNELS="${OPENCLAW_SKIP_CHANNELS:-1}"
export OPENCLAW_SKIP_PROVIDERS="${OPENCLAW_SKIP_PROVIDERS:-1}"
export OPENCLAW_PLUGIN_STAGE_DIR="${OPENCLAW_PLUGIN_STAGE_DIR:-/home/node/.openclaw/plugin-runtime-deps}"
export OPENCLAW_NO_RESPAWN=1
export CLAWBENCH_DISABLE_GATEWAY_DEVICE_IDENTITY="${CLAWBENCH_DISABLE_GATEWAY_DEVICE_IDENTITY:-1}"
export CLAWBENCH_PER_RUN_BUDGET_SECONDS
export CLAWBENCH_PER_TURN_TIMEOUT_SECONDS
export CLAWBENCH_CONNECT_TIMEOUT="${CLAWBENCH_CONNECT_TIMEOUT:-180}"
export CLAWBENCH_REQUEST_TIMEOUT="${CLAWBENCH_REQUEST_TIMEOUT:-300}"
export CLAWBENCH_GATEWAY_HEALTH_TIMEOUT_SECONDS="${CLAWBENCH_GATEWAY_HEALTH_TIMEOUT_SECONDS:-240}"
export CLAWBENCH_LANE_STARTUP_STAGGER_SECONDS="${CLAWBENCH_LANE_STARTUP_STAGGER_SECONDS:-90}"
export CLAWBENCH_GATEWAY_READY_MARKER_GRACE_SECONDS="${CLAWBENCH_GATEWAY_READY_MARKER_GRACE_SECONDS:-90}"
export CLAWBENCH_KEEP_PARALLEL_LANE_ROOT="${CLAWBENCH_KEEP_PARALLEL_LANE_ROOT:-0}"
export CLAWBENCH_PARALLEL_LANE_ROOT="/data/lane_runtime/$SWEEP_LABEL"
export CLAWBENCH_TOOL_PROFILE_NAME="${CLAWBENCH_TOOL_PROFILE_NAME:-$SWEEP_LABEL}"
export NODE_OPTIONS="${NODE_OPTIONS:-"--max-old-space-size=4096"}"
if command -v npm >/dev/null 2>&1; then
  export NODE_PATH="${NODE_PATH:-$(npm root -g 2>/dev/null || true)}"
fi

if [ -n "${CLAWBENCH_TASK_TIMEOUT_SCALE:-}" ] && [ "${CLAWBENCH_TASK_TIMEOUT_SCALE:-1}" != "1" ] && [ "${CLAWBENCH_TASK_TIMEOUT_SCALE:-1.0}" != "1.0" ]; then
  SOURCE_TASKS_DIR="${CLAWBENCH_TASKS_DIR:-/tasks}"
  SCALED_TASKS_DIR="/tmp/clawbench-tasks-scaled-${SWEEP_LABEL}-$$"
  rm -rf "$SCALED_TASKS_DIR"
  mkdir -p "$SCALED_TASKS_DIR"
  cp -a "$SOURCE_TASKS_DIR/." "$SCALED_TASKS_DIR/"
  python3 - "$SCALED_TASKS_DIR" "$CLAWBENCH_TASK_TIMEOUT_SCALE" <<'PY'
import pathlib
import re
import sys

tasks_dir = pathlib.Path(sys.argv[1])
scale = float(sys.argv[2])
if scale <= 0 or scale > 20:
    raise SystemExit(f"invalid CLAWBENCH_TASK_TIMEOUT_SCALE={scale}")

touched = 0
for yml in tasks_dir.rglob("t*.yaml"):
    raw = yml.read_text(encoding="utf-8")

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        original = int(match.group(2))
        scaled = max(1, int(round(original * scale)))
        return f"{key}: {scaled}"

    new = re.sub(r"^(timeout_seconds):\s*(\d+)\s*$", repl, raw, flags=re.MULTILINE)
    new = re.sub(r"^(    timeout_seconds):\s*(\d+)\s*$", repl, new, flags=re.MULTILINE)
    new = re.sub(r"^(  timeout_seconds):\s*(\d+)\s*$", repl, new, flags=re.MULTILINE)
    if new != raw:
        yml.write_text(new, encoding="utf-8")
        touched += 1
print(f"scaled task timeouts in {touched} files by {scale}x")
PY
  export CLAWBENCH_TASKS_DIR="$SCALED_TASKS_DIR"
fi

SRC_STATE="${OPENCLAW_CONFIG_SOURCE:-/config/openclaw}"
if [ ! -d "$SRC_STATE" ]; then
  SRC_STATE="/home/node/.openclaw"
fi

safe_model="${SWEEP_MODEL//\//_}"
safe_model="${safe_model//:/_}"
OUT="$SWEEP_LOGDIR/${SWEEP_LABEL}_openclaw_${safe_model}_${SWEEP_OUT_TAG}.json"
LOG="$SWEEP_LOGDIR/${SWEEP_LABEL}_openclaw_${safe_model}_${SWEEP_OUT_TAG}.log"
export SWEEP_OUTPUT_PATH="$OUT"

FRESH_HOME="/tmp/openclaw-home-${SWEEP_LABEL}-$$"
FRESH_STATE="$FRESH_HOME/.openclaw"
rm -rf "$FRESH_HOME" "$CLAWBENCH_PARALLEL_LANE_ROOT"
mkdir -p "$FRESH_STATE" "$FRESH_HOME/.config"
if [ -f "$SRC_STATE/openclaw.json" ]; then
  cp "$SRC_STATE/openclaw.json" "$FRESH_STATE/openclaw.json"
fi
if [ -d "$SRC_STATE/plugins" ]; then
  mkdir -p "$FRESH_STATE/plugins"
  cp -R "$SRC_STATE/plugins/." "$FRESH_STATE/plugins/" 2>/dev/null || true
fi
if [ -d "$SRC_STATE/agents" ]; then
  mkdir -p "$FRESH_STATE/agents"
  cp -R "$SRC_STATE/agents/." "$FRESH_STATE/agents/" 2>/dev/null || true
fi
CODEX_STATE_SOURCE="${CODEX_CONFIG_SOURCE:-/config/codex}"
if [ -d "$CODEX_STATE_SOURCE" ]; then
  mkdir -p "$FRESH_HOME/.codex"
  for codex_file in auth.json config.toml; do
    if [ -f "$CODEX_STATE_SOURCE/$codex_file" ]; then
      cp "$CODEX_STATE_SOURCE/$codex_file" "$FRESH_HOME/.codex/$codex_file"
      chmod 600 "$FRESH_HOME/.codex/$codex_file" 2>/dev/null || true
    fi
  done
fi
mkdir -p \
  "$FRESH_STATE/agents" \
  "$FRESH_STATE/workspace" \
  "$FRESH_STATE/logs" \
  "$FRESH_STATE/memory" \
  "$FRESH_STATE/cache" \
  "$FRESH_STATE/identity" \
  "$FRESH_STATE/devices" \
  "$FRESH_STATE/tasks" \
  "$FRESH_STATE/subagents" \
  "$FRESH_STATE/flows" \
  "$FRESH_STATE/cron"

export HOME="$FRESH_HOME"
export OPENCLAW_HOME="$FRESH_HOME"
export OPENCLAW_STATE_DIR="$FRESH_STATE"
export OPENCLAW_CONFIG_PATH="$FRESH_STATE/openclaw.json"
export XDG_CONFIG_HOME="$FRESH_HOME/.config"
SWEEP_AGENT_RUNTIME="${SWEEP_AGENT_RUNTIME:-${CLAWBENCH_OPENCLAW_AGENT_RUNTIME:-${OPENCLAW_AGENT_RUNTIME:-}}}"
if [ -n "$SWEEP_AGENT_RUNTIME" ]; then
  export OPENCLAW_AGENT_RUNTIME="$SWEEP_AGENT_RUNTIME"
  export CLAWBENCH_OPENCLAW_AGENT_RUNTIME="$SWEEP_AGENT_RUNTIME"
fi
case "$SWEEP_MODEL:${SWEEP_AGENT_RUNTIME:-}" in
  *:codex|codex/*)
    export OPENCLAW_CODEX_APP_SERVER_MODE="${OPENCLAW_CODEX_APP_SERVER_MODE:-yolo}"
    export OPENCLAW_CODEX_APP_SERVER_APPROVAL_POLICY="${OPENCLAW_CODEX_APP_SERVER_APPROVAL_POLICY:-never}"
    export OPENCLAW_CODEX_APP_SERVER_SANDBOX="${OPENCLAW_CODEX_APP_SERVER_SANDBOX:-danger-full-access}"
    ;;
esac
case "$SWEEP_MODEL" in
  codex/*)
    export OPENCLAW_CODEX_APP_SERVER_MODE="${OPENCLAW_CODEX_APP_SERVER_MODE:-yolo}"
    export OPENCLAW_CODEX_APP_SERVER_APPROVAL_POLICY="${OPENCLAW_CODEX_APP_SERVER_APPROVAL_POLICY:-never}"
    export OPENCLAW_CODEX_APP_SERVER_SANDBOX="${OPENCLAW_CODEX_APP_SERVER_SANDBOX:-danger-full-access}"
    ;;
esac

python - <<'PY'
import json
import os
from pathlib import Path

cfg_path = Path(os.environ["OPENCLAW_CONFIG_PATH"])
if not cfg_path.exists():
    raise SystemExit("missing openclaw.json")
data = json.loads(cfg_path.read_text(encoding="utf-8"))

def set_nested(root, dotted, value):
    cursor = root
    parts = dotted.split(".")
    for part in parts[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            child = {}
            cursor[part] = child
        cursor = child
    cursor[parts[-1]] = value


def set_model_agent_runtime_policy(root, model_ref, agent_runtime):
    agents = root.setdefault("agents", {})
    if not isinstance(agents, dict):
        return
    defaults = agents.setdefault("defaults", {})
    if not isinstance(defaults, dict):
        return
    models = defaults.setdefault("models", {})
    if not isinstance(models, dict):
        return
    model_cfg = models.setdefault(model_ref, {})
    if not isinstance(model_cfg, dict):
        model_cfg = {}
        models[model_ref] = model_cfg
    model_cfg["agentRuntime"] = {"id": agent_runtime}

    if agent_runtime == "codex":
        plugins_cfg = root.setdefault("plugins", {})
        if isinstance(plugins_cfg, dict):
            allow = plugins_cfg.get("allow")
            if isinstance(allow, list) and "codex" not in allow:
                allow.append("codex")


def strip_agent_runtime_policy(root):
    agents = root.get("agents")
    if not isinstance(agents, dict):
        return
    defaults = agents.get("defaults")
    if not isinstance(defaults, dict):
        return
    defaults.pop("agentRuntime", None)
    models = defaults.get("models")
    if isinstance(models, dict):
        for model_cfg in models.values():
            if isinstance(model_cfg, dict):
                model_cfg.pop("agentRuntime", None)


def ensure_legacy_openai_model(root, model_ref):
    # OpenClaw 2026.4.x forward-fills gpt-5.4, but it predates gpt-5.5.
    # Ensure legacy OpenAI provider config has the API key env var, then seed
    # only the requested missing legacy model.
    if not model_ref.startswith("openai/"):
        return
    models_cfg = root.setdefault("models", {})
    if not isinstance(models_cfg, dict):
        return
    providers = models_cfg.setdefault("providers", {})
    if not isinstance(providers, dict):
        return
    provider_cfg = providers.get("openai")
    if not isinstance(provider_cfg, dict):
        provider_cfg = {}
        providers["openai"] = provider_cfg
    provider_cfg.setdefault("baseUrl", "https://api.openai.com/v1")
    provider_cfg.setdefault("api", "openai-responses")
    provider_cfg.setdefault("apiKey", "OPENAI_API_KEY")
    legacy_model_ids = {"openai/gpt-5.4": "gpt-5.4", "openai/gpt-5.5": "gpt-5.5"}
    model_id = legacy_model_ids.get(model_ref)
    if not model_id:
        return
    model_entries = provider_cfg.get("models")
    if not isinstance(model_entries, list):
        model_entries = []
        provider_cfg["models"] = model_entries
    if not any(isinstance(item, dict) and item.get("id") == model_id for item in model_entries):
        model_entries.append(
            {
                "id": model_id,
                "name": model_id,
                "api": "openai-responses",
                "reasoning": True,
                "input": ["text", "image"],
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "contextWindow": 1050000,
                "maxTokens": 128000,
            }
        )


def sanitize_legacy_plugins(root, model_ref):
    plugins_cfg = root.get("plugins")
    if not isinstance(plugins_cfg, dict):
        return
    legacy_stale = {"openai"}
    if not model_ref.startswith("codex/"):
        legacy_stale.add("codex")
    allow = plugins_cfg.get("allow")
    if isinstance(allow, list):
        plugins_cfg["allow"] = [item for item in allow if item not in legacy_stale]
    entries = plugins_cfg.get("entries")
    if isinstance(entries, dict):
        for item in legacy_stale:
            entries.pop(item, None)


def ensure_codex_dynamic_tools_config(root, loading):
    if loading not in {"searchable", "direct"}:
        raise SystemExit(f"invalid CLAWBENCH_CODEX_DYNAMIC_TOOLS_LOADING={loading!r}")
    plugins_cfg = root.setdefault("plugins", {})
    if not isinstance(plugins_cfg, dict):
        return
    allow = plugins_cfg.setdefault("allow", [])
    if isinstance(allow, list) and "codex" not in allow:
        allow.append("codex")
    entries = plugins_cfg.setdefault("entries", {})
    if not isinstance(entries, dict):
        entries = {}
        plugins_cfg["entries"] = entries
    codex = entries.setdefault("codex", {})
    if not isinstance(codex, dict):
        codex = {}
        entries["codex"] = codex
    config = codex.setdefault("config", {})
    if not isinstance(config, dict):
        config = {}
        codex["config"] = config
    config["codexDynamicToolsLoading"] = loading


def parse_optional_bool_env(name):
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"invalid {name}={raw!r}; expected true or false")

agents = data.setdefault("agents", {})
if isinstance(agents, dict):
    agents["list"] = []

channels = data.get("channels")
if isinstance(channels, dict):
    channels.pop("whatsapp", None)
    for channel in channels.values():
        if isinstance(channel, dict):
            channel["enabled"] = False
            streaming = channel.get("streaming")
            if isinstance(streaming, dict):
                streaming["mode"] = "off"
            else:
                channel["streaming"] = {"mode": "off"}
            exec_approvals = channel.get("execApprovals")
            if not isinstance(exec_approvals, dict):
                exec_approvals = {}
                channel["execApprovals"] = exec_approvals
            exec_approvals["enabled"] = False

plugins = data.setdefault("plugins", {})
stale = {"marxbiotech-git-tools", "lab", "whatsapp"}
allow = plugins.get("allow")
if isinstance(allow, list):
    plugins["allow"] = [item for item in allow if item not in stale]
entries = plugins.get("entries")
if isinstance(entries, dict):
    for item in stale:
        entries.pop(item, None)

set_nested(data, "browser.headless", True)
set_nested(data, "browser.noSandbox", True)
set_nested(data, "gateway.reload.mode", "off")
set_nested(data, "agents.defaults.skipBootstrap", True)
set_nested(data, "agents.defaults.sandbox.mode", "off")
set_nested(data, "agents.defaults.model.primary", os.environ["SWEEP_MODEL"])
set_nested(data, "agents.defaults.subagents.model.primary", os.environ["SWEEP_MODEL"])
set_nested(data, "tools.exec.host", os.environ.get("OPENCLAW_EXEC_HOST", "gateway"))
set_nested(data, "tools.exec.security", "full")
set_nested(data, "tools.exec.ask", "off")
set_nested(data, "approvals.exec.enabled", False)
model_ref = os.environ["SWEEP_MODEL"].strip()
agent_runtime = os.environ.get("SWEEP_AGENT_RUNTIME", "").strip()
legacy_config = os.environ.get("CLAWBENCH_OPENCLAW_LEGACY_CONFIG", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
if agent_runtime and not legacy_config:
    set_nested(data, "agents.defaults.agentRuntime.id", agent_runtime)
    set_model_agent_runtime_policy(data, model_ref, agent_runtime)
elif legacy_config:
    strip_agent_runtime_policy(data)
    ensure_legacy_openai_model(data, model_ref)
    sanitize_legacy_plugins(data, model_ref)
else:
    strip_agent_runtime_policy(data)
    sanitize_legacy_plugins(data, model_ref)
if agent_runtime == "codex" or model_ref.startswith("codex/"):
    ensure_codex_dynamic_tools_config(
        data,
        os.environ.get("CLAWBENCH_CODEX_DYNAMIC_TOOLS_LOADING", "searchable").strip(),
    )
tool_search_enabled = parse_optional_bool_env("CLAWBENCH_OPENCLAW_TOOL_SEARCH")
if tool_search_enabled is not None:
    set_nested(data, "tools.toolSearch", tool_search_enabled)

if model_ref.startswith("openrouter/") and len(model_ref.split("/", 1)) == 2:
    model_id = model_ref.split("/", 1)[1]
    openrouter_timeout_seconds = int(os.environ.get("CLAWBENCH_OPENROUTER_TIMEOUT_SECONDS") or "900")
    set_nested(
        data,
        "agents.defaults.thinkingDefault",
        os.environ.get("CLAWBENCH_OPENROUTER_THINKING_DEFAULT", "low").strip() or "low",
    )
    set_nested(
        data,
        "agents.defaults.timeoutSeconds",
        int(os.environ.get("CLAWBENCH_OPENROUTER_AGENT_TIMEOUT_SECONDS") or "1200"),
    )
    agents_cfg = data.setdefault("agents", {})
    if isinstance(agents_cfg, dict):
        defaults_cfg = agents_cfg.setdefault("defaults", {})
        if isinstance(defaults_cfg, dict):
            model_defaults = defaults_cfg.setdefault("models", {})
            if isinstance(model_defaults, dict):
                model_cfg = model_defaults.setdefault(model_ref, {})
                if not isinstance(model_cfg, dict):
                    model_cfg = {}
                    model_defaults[model_ref] = model_cfg
                params_cfg = model_cfg.setdefault("params", {})
                if not isinstance(params_cfg, dict):
                    params_cfg = {}
                    model_cfg["params"] = params_cfg
                extra_body = params_cfg.setdefault("extra_body", {})
                if not isinstance(extra_body, dict):
                    extra_body = {}
                    params_cfg["extra_body"] = extra_body
                extra_body["include_reasoning"] = False
                extra_body["reasoning"] = {"exclude": True}
    models_cfg = data.setdefault("models", {})
    if isinstance(models_cfg, dict):
        providers = models_cfg.setdefault("providers", {})
        if isinstance(providers, dict):
            provider_cfg = providers.get("openrouter")
            if not isinstance(provider_cfg, dict):
                provider_cfg = {}
                providers["openrouter"] = provider_cfg
            provider_cfg.setdefault("baseUrl", "https://openrouter.ai/api/v1")
            provider_cfg["api"] = "openai-completions"
            provider_cfg.setdefault("apiKey", "OPENROUTER_API_KEY")
            provider_cfg["timeoutSeconds"] = openrouter_timeout_seconds
            model_entries = provider_cfg.get("models")
            if not isinstance(model_entries, list):
                model_entries = []
                provider_cfg["models"] = model_entries
            desired_model = {
                "id": model_id,
                "name": model_id,
                "contextWindow": 131072,
                "maxTokens": 8192,
            }
            for item in model_entries:
                if isinstance(item, dict) and item.get("id") == model_id:
                    item.update(desired_model)
                    break
            else:
                model_entries.append(desired_model)

cfg_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

approvals_path = cfg_path.with_name("exec-approvals.json")
approvals = {
    "version": 1,
    "socket": {
        "path": str(approvals_path.with_suffix(".sock")),
        "token": "container-lane-eval-token",
    },
    "defaults": {"security": "full", "ask": "off", "askFallback": "full"},
    "agents": {"*": {"security": "full", "ask": "off", "askFallback": "full"}},
}
approvals_path.write_text(json.dumps(approvals, indent=2) + "\n", encoding="utf-8")
PY

if [ "${CLAWBENCH_ENABLE_GBRAIN:-0}" = "1" ]; then
  export CLAWBENCH_LANE_PREPARE_CMD="${CLAWBENCH_LANE_PREPARE_CMD:-/home/node/app/scripts/setup_gbrain_runtime.sh}"
  "$CLAWBENCH_LANE_PREPARE_CMD"
fi

echo "===== CONTAINER LANE EVAL START $(date '+%Y-%m-%d %H:%M:%S') ====="
echo "label:    $SWEEP_LABEL"
echo "model:    $SWEEP_MODEL"
echo "runtime:  ${SWEEP_AGENT_RUNTIME:-default}"
echo "codex dynamic tools: ${CLAWBENCH_CODEX_DYNAMIC_TOOLS_LOADING:-default}"
echo "pi tool search: ${CLAWBENCH_OPENCLAW_TOOL_SEARCH:-default}"
echo "runs:     $SWEEP_RUNS"
echo "lanes:    $SWEEP_LANES"
echo "tasks:    ${SWEEP_TASKS:-${CHERRY_TASKS:-all}}"
echo "out:      $OUT"
echo "log:      $LOG"
echo "home:     $HOME"
echo "state:    $OPENCLAW_STATE_DIR"
openclaw --version 2>/dev/null || true

set +e
python - <<'PY' > "$LOG" 2>&1
import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

from clawbench.queue import JobQueue, JobStatus, SubmissionRequest
from clawbench.worker import EvalWorker, RESULTS_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

async def main() -> int:
    queue = JobQueue()
    queue._jobs.clear()
    queue._save_local()
    task_ids_raw = os.environ.get("SWEEP_TASKS") or os.environ.get("CHERRY_TASKS") or ""
    task_ids = [item.strip() for item in task_ids_raw.split(",") if item.strip()]
    request = SubmissionRequest(
        model=os.environ["SWEEP_MODEL"],
        runs_per_task=int(os.environ["SWEEP_RUNS"]),
        max_parallel_lanes=int(os.environ["SWEEP_LANES"]),
        task_ids=task_ids,
        prompt_variant=os.environ.get("SWEEP_PROMPT_VARIANT", "clear"),
        judge_model=os.environ.get("CLAWBENCH_JUDGE_MODEL", ""),
        notes=os.environ.get("SWEEP_LABEL", ""),
    )
    job = await queue.submit(request)
    worker = EvalWorker(queue)
    await worker._process_job(job)
    final = await queue.get_status(job.job_id)
    print(json.dumps(final.model_dump() if final else {}, indent=2), flush=True)
    if final is None or final.status != JobStatus.FINISHED or not final.result_id:
        return 1
    result_path = RESULTS_DIR / f"{final.result_id}.json"
    output_path = Path(os.environ["SWEEP_OUTPUT_PATH"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(result_path, output_path)
    return 0

raise SystemExit(asyncio.run(main()))
PY
status=$?
set -e

echo "===== lane eval exit=$status $(date '+%Y-%m-%d %H:%M:%S') ====="
tail -120 "$LOG" 2>/dev/null || true
exit "$status"
