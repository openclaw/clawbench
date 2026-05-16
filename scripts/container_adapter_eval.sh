#!/bin/bash
# Fair adapter lane runner.
#
# Runs one adapter/model pair inside a container-owned workspace/state dir.
# Use docker run with full container privileges when measuring harnesses:
#   docker run --rm --privileged --cap-add=ALL \
#     --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
#     --user root --env-file .tmp/docker_eval.env \
#     -e SWEEP_ADAPTER=hermes -e SWEEP_MODEL=openai/gpt-5.4 \
#     -e SWEEP_LABEL=hermes-gpt54 -e SWEEP_OUT_TAG=fair-20260425 \
#     -v "$PWD/data/fair-container:/data" \
#     -v "$PWD/data/container-home-openclaw:/config/openclaw:ro" \
#     clawbench-fair:latest

set -u

: "${SWEEP_ADAPTER:?SWEEP_ADAPTER required (openclaw|hermes)}"
: "${SWEEP_MODEL:?SWEEP_MODEL required (e.g. openai/gpt-5.4)}"
: "${SWEEP_LABEL:?SWEEP_LABEL required}"
: "${SWEEP_OUT_TAG:=fair-container}"
: "${SWEEP_LOGDIR:=/data/fair_results}"
: "${SWEEP_RUNS:=1}"
: "${SWEEP_CONCURRENCY:=1}"
: "${SWEEP_BROWSER_CONCURRENCY:=1}"
: "${CLAWBENCH_PER_RUN_BUDGET_SECONDS:=300}"
: "${CLAWBENCH_PER_TURN_TIMEOUT_SECONDS:=180}"
: "${HERMES_MAX_ITERATIONS:=90}"
: "${HERMES_STEP_TIMEOUT_SECONDS:=60}"
: "${OPENCLAW_EXEC_HOST:=gateway}"
: "${CLAWBENCH_CODEX_DYNAMIC_TOOLS_LOADING:=searchable}"

cd /home/node/app
mkdir -p "$SWEEP_LOGDIR" /data/run_cache

export OPENCLAW_GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-local-dev-token-for-testing}"
export OPENCLAW_GATEWAY_URL="${OPENCLAW_GATEWAY_URL:-ws://127.0.0.1:18789}"
export OPENCLAW_SKIP_GMAIL_WATCHER=1
export OPENCLAW_SKIP_CANVAS_HOST=1
export OPENCLAW_NO_RESPAWN=1
export CLAWBENCH_DISABLE_GATEWAY_DEVICE_IDENTITY="${CLAWBENCH_DISABLE_GATEWAY_DEVICE_IDENTITY:-0}"
export NODE_OPTIONS="${NODE_OPTIONS:-"--max-old-space-size=4096"}"
if command -v npm >/dev/null 2>&1; then
  export NODE_PATH="${NODE_PATH:-$(npm root -g 2>/dev/null || true)}"
fi
export CLAWBENCH_PER_RUN_BUDGET_SECONDS
export CLAWBENCH_PER_TURN_TIMEOUT_SECONDS
export HERMES_AGENT_REPO="${HERMES_AGENT_REPO:-/opt/hermes-agent}"
export HERMES_DRIVER="${HERMES_DRIVER:-ai_agent}"
export HERMES_TOOLSETS="${HERMES_TOOLSETS:-hermes-api-server}"
export HERMES_MAX_ITERATIONS
export HERMES_STEP_TIMEOUT_SECONDS
export TERMINAL_ENV="${TERMINAL_ENV:-local}"

write_eval_exec_approvals() {
  python - "$FRESH_STATE" <<'PY'
import json
import sys
from pathlib import Path

state_dir = Path(sys.argv[1])
state_dir.mkdir(parents=True, exist_ok=True)
approvals_path = state_dir / "exec-approvals.json"
approvals = {
    "version": 1,
    "socket": {
        "path": str(approvals_path.with_suffix(".sock")),
        "token": "container-eval-token",
    },
    "defaults": {
        "security": "full",
        "ask": "off",
        "askFallback": "full",
    },
    "agents": {
        "*": {
            "security": "full",
            "ask": "off",
            "askFallback": "full",
        }
    },
}
tmp_path = approvals_path.with_suffix(".json.tmp")
tmp_path.write_text(json.dumps(approvals, indent=2) + "\n", encoding="utf-8")
tmp_path.replace(approvals_path)
PY
}

safe_model="${SWEEP_MODEL//\//_}"
safe_model="${safe_model//:/_}"
safe_label="${SWEEP_LABEL//\//_}"
safe_label="${safe_label//:/_}"
export CLAWBENCH_RUN_CACHE_DIR="/data/run_cache/$safe_label"
mkdir -p "$CLAWBENCH_RUN_CACHE_DIR"
cache_sub="${SWEEP_ADAPTER}-${safe_model}"
cache_paths=("$CLAWBENCH_RUN_CACHE_DIR/$cache_sub")
if [ "$SWEEP_ADAPTER" = "openclaw" ]; then
  cache_paths+=("$CLAWBENCH_RUN_CACHE_DIR/$safe_model")
fi

SRC_STATE="${OPENCLAW_CONFIG_SOURCE:-/config/openclaw}"
if [ ! -d "$SRC_STATE" ]; then
  SRC_STATE="/home/node/.openclaw"
fi
FRESH_HOME="/tmp/openclaw-home-${SWEEP_LABEL}-$$"
FRESH_STATE="$FRESH_HOME/.openclaw"
rm -rf "$FRESH_HOME"
mkdir -p "$FRESH_STATE" "$FRESH_HOME/.config"
if [ -f "$SRC_STATE/openclaw.json" ]; then
  cp "$SRC_STATE/openclaw.json" "$FRESH_STATE/openclaw.json"
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
chmod -R 777 "$FRESH_STATE" 2>/dev/null || true
export HOME="$FRESH_HOME"
export OPENCLAW_HOME="$FRESH_HOME"
export OPENCLAW_STATE_DIR="$FRESH_STATE"
export OPENCLAW_CONFIG_PATH="$FRESH_STATE/openclaw.json"
export OPENCLAW_REPO="${OPENCLAW_REPO:-/app}"
export XDG_CONFIG_HOME="$FRESH_HOME/.config"
export HERMES_HOME_BASE="${HERMES_HOME_BASE:-$FRESH_HOME/.hermes}"
export HERMES_HOME="$HERMES_HOME_BASE"
mkdir -p "$HERMES_HOME"
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

if [ "$SWEEP_ADAPTER" = "hermes" ]; then
  unset HERMES_PROVIDER
  case "$SWEEP_MODEL" in
    openai/*)
      if [ -z "${OPENAI_API_KEY:-}" ] && [ -n "${HERMES_API_KEY:-}" ]; then
        export OPENAI_API_KEY="$HERMES_API_KEY"
      fi
      export HERMES_BASE_URL="${HERMES_BASE_URL:-${OPENAI_BASE_URL:-https://api.openai.com/v1}}"
      export OPENAI_BASE_URL="$HERMES_BASE_URL"
      if [ -n "${OPENAI_API_KEY:-}" ]; then
        export HERMES_API_KEY="$OPENAI_API_KEY"
      fi
      unset ANTHROPIC_API_KEY ANTHROPIC_TOKEN CLAUDE_CODE_OAUTH_TOKEN OPENROUTER_API_KEY
      ;;
    anthropic/*)
      unset OPENAI_API_KEY OPENAI_BASE_URL HERMES_API_KEY HERMES_BASE_URL OPENROUTER_API_KEY
      ;;
    *)
      if [ -n "${HERMES_BASE_URL:-}" ]; then
        export OPENAI_BASE_URL="$HERMES_BASE_URL"
      elif [ -z "${OPENAI_BASE_URL:-}" ] && [ -n "${OPENAI_API_KEY:-}" ]; then
        export OPENAI_BASE_URL="https://api.openai.com/v1"
      fi
      if [ -n "${HERMES_API_KEY:-}" ] && [ -z "${OPENAI_API_KEY:-}" ]; then
        export OPENAI_API_KEY="$HERMES_API_KEY"
      fi
      ;;
  esac
fi

python - <<'PY'
import json
import os
from pathlib import Path

cfg_path = Path(os.environ["OPENCLAW_CONFIG_PATH"])
if not cfg_path.exists():
    raise SystemExit(0)

data = json.loads(cfg_path.read_text(encoding="utf-8"))

agents = data.get("agents")
if isinstance(agents, dict):
    # Keep static defaults, but never seed eval containers with old session-specific
    # agent records from the developer machine.
    agents["list"] = []

channels = data.get("channels")
if isinstance(channels, dict):
    channels.pop("whatsapp", None)
    for channel in channels.values():
        if isinstance(channel, dict):
            channel["enabled"] = False
            exec_approvals = channel.get("execApprovals")
            if not isinstance(exec_approvals, dict):
                exec_approvals = {}
                channel["execApprovals"] = exec_approvals
            exec_approvals["enabled"] = False

plugins = data.get("plugins")
if isinstance(plugins, dict):
    stale = {"marxbiotech-git-tools", "lab", "whatsapp"}
    allow = plugins.get("allow")
    if isinstance(allow, list):
        plugins["allow"] = [item for item in allow if item not in stale]
    entries = plugins.get("entries")
    if isinstance(entries, dict):
        for item in stale:
            entries.pop(item, None)


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
    models = defaults.get("models")
    if not isinstance(models, dict):
        models = {}
    for model_cfg in models.values():
        if isinstance(model_cfg, dict):
            model_cfg.pop("agentRuntime", None)

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


def ensure_codex_plugin_allowed(root, loading):
    if loading not in {"searchable", "direct"}:
        raise SystemExit(f"invalid CLAWBENCH_CODEX_DYNAMIC_TOOLS_LOADING={loading!r}")
    plugins_cfg = root.setdefault("plugins", {})
    if not isinstance(plugins_cfg, dict):
        return
    allow = plugins_cfg.setdefault("allow", [])
    if isinstance(allow, list) and "codex" not in allow:
        allow.append("codex")
    entries = plugins_cfg.get("entries")
    if isinstance(entries, dict):
        codex = entries.get("codex")
        if isinstance(codex, dict):
            codex.pop("config", None)


def parse_optional_bool_env(name):
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"invalid {name}={raw!r}; expected true or false")


set_nested(data, "browser.headless", True)
set_nested(data, "browser.noSandbox", True)
set_nested(data, "gateway.reload.mode", "off")
set_nested(data, "agents.defaults.skipBootstrap", True)
set_nested(data, "agents.defaults.sandbox.mode", "off")
exec_host = os.environ.get("OPENCLAW_EXEC_HOST", "gateway").strip().lower()
if exec_host not in {"auto", "gateway", "sandbox", "node"}:
    raise SystemExit(f"invalid OPENCLAW_EXEC_HOST={exec_host!r}")
set_nested(data, "tools.exec.host", exec_host)
set_nested(data, "tools.exec.security", "full")
set_nested(data, "tools.exec.ask", "off")
set_nested(data, "approvals.exec.enabled", False)
if parse_optional_bool_env("CLAWBENCH_DISABLE_GATEWAY_DEVICE_IDENTITY") is True:
    set_nested(data, "gateway.controlUi.allowInsecureAuth", True)
    set_nested(data, "gateway.controlUi.dangerouslyDisableDeviceAuth", True)
model = os.environ.get("SWEEP_MODEL", "").strip()
if model:
    set_nested(data, "agents.defaults.model.primary", model)
    set_nested(data, "agents.defaults.subagents.model.primary", model)
agent_runtime = os.environ.get("SWEEP_AGENT_RUNTIME", "").strip()
legacy_config = os.environ.get("CLAWBENCH_OPENCLAW_LEGACY_CONFIG", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
if agent_runtime and not legacy_config:
    set_nested(data, "agents.defaults.agentRuntime.id", agent_runtime)
    if model:
        set_model_agent_runtime_policy(data, model, agent_runtime)
elif legacy_config:
    strip_agent_runtime_policy(data)
if agent_runtime == "codex" or model.startswith("codex/"):
    ensure_codex_plugin_allowed(
        data,
        os.environ.get("CLAWBENCH_CODEX_DYNAMIC_TOOLS_LOADING", "searchable").strip(),
    )
tool_search_enabled = parse_optional_bool_env("CLAWBENCH_OPENCLAW_TOOL_SEARCH")
if tool_search_enabled is not None:
    set_nested(data, "tools.toolSearch", tool_search_enabled)

tmp_path = cfg_path.with_suffix(".json.tmp")
tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
tmp_path.replace(cfg_path)
PY
write_eval_exec_approvals || exit 1

if [ "$SWEEP_ADAPTER" = "hermes" ]; then
python - <<'PY'
import os
from pathlib import Path
from urllib.parse import urlparse

model = os.environ["SWEEP_MODEL"].strip()
base_url = (os.environ.get("HERMES_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "").strip()

provider = "custom"
effective_model = model
aux_base_url = ""
aux_api_mode = ""
if model.startswith("anthropic/"):
    provider = "anthropic"
elif urlparse(base_url).hostname == "api.openai.com" and model.startswith("openai/"):
    effective_model = model.split("/", 1)[1]
    aux_base_url = base_url
    if effective_model.lower().startswith("gpt-5"):
        aux_api_mode = "codex_responses"
elif base_url:
    aux_base_url = base_url

tasks = [
    "vision",
    "web_extract",
    "compression",
    "session_search",
    "skills_hub",
    "approval",
    "mcp",
    "title_generation",
]

lines = [
    "model:",
    f"  provider: {provider}",
    f"  default: {effective_model}",
]
if aux_base_url:
    lines.append(f"  base_url: {aux_base_url}")
if aux_api_mode:
    lines.append(f"  api_mode: {aux_api_mode}")
lines.append("auxiliary:")
for task in tasks:
    timeout = 360 if task == "web_extract" else 120 if task in {"vision", "compression"} else 30
    lines.extend([
        f"  {task}:",
        "    provider: main",
        f"    model: {effective_model}",
        f"    timeout: {timeout}",
    ])
    if aux_base_url:
        lines.append(f"    base_url: {aux_base_url}")
    if aux_api_mode:
        lines.append(f"    api_mode: {aux_api_mode}")
    if task == "session_search":
        lines.append("    max_concurrency: 1")

path = Path(os.environ["HERMES_HOME"]) / "config.yaml"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
fi

OUT="$SWEEP_LOGDIR/${SWEEP_LABEL}_${SWEEP_ADAPTER}_${safe_model}_${SWEEP_OUT_TAG}.json"
LOG="$SWEEP_LOGDIR/${SWEEP_LABEL}_${SWEEP_ADAPTER}_${safe_model}_${SWEEP_OUT_TAG}.log"
GWLOG="$SWEEP_LOGDIR/gateway_${SWEEP_LABEL}_${SWEEP_OUT_TAG}.log"
HERMES_AGENT_LOG="$SWEEP_LOGDIR/hermes_agent_${SWEEP_LABEL}_${SWEEP_OUT_TAG}.log"
HERMES_ERROR_LOG="$SWEEP_LOGDIR/hermes_errors_${SWEEP_LABEL}_${SWEEP_OUT_TAG}.log"

echo "===== CONTAINER ADAPTER EVAL START $(date '+%Y-%m-%d %H:%M:%S') ====="
echo "uid:      $(id -u) ($(id -un 2>/dev/null || true))"
echo "adapter:  $SWEEP_ADAPTER"
echo "model:    $SWEEP_MODEL"
echo "runtime:  ${SWEEP_AGENT_RUNTIME:-default}"
echo "codex dynamic tools: ${CLAWBENCH_CODEX_DYNAMIC_TOOLS_LOADING:-default}"
echo "pi tool search: ${CLAWBENCH_OPENCLAW_TOOL_SEARCH:-default}"
echo "runs:     $SWEEP_RUNS"
echo "execHost: $OPENCLAW_EXEC_HOST"
echo "out:      $OUT"
echo "cache:    ${cache_paths[*]}"
echo "home:     $HOME"
echo "state:    $OPENCLAW_STATE_DIR"
echo "hermes:   ${HERMES_HOME:-}"
openclaw --version 2>/dev/null || true
python - <<'PY' 2>/dev/null || true
import os, subprocess
repo = os.environ.get("HERMES_AGENT_REPO", "")
if repo:
    try:
        sha = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()
        print(f"Hermes git: {sha}")
    except Exception:
        print(f"Hermes repo: {repo}")
PY

rm -rf "${cache_paths[@]}"
rm -f "$OUT" "$LOG"

GATEWAY_PID=""
preserve_hermes_logs() {
  if [ -f "${HERMES_HOME:-}/logs/agent.log" ]; then
    cp "${HERMES_HOME:-}/logs/agent.log" "$HERMES_AGENT_LOG" 2>/dev/null || true
  fi
  if [ -f "${HERMES_HOME:-}/logs/errors.log" ]; then
    cp "${HERMES_HOME:-}/logs/errors.log" "$HERMES_ERROR_LOG" 2>/dev/null || true
  fi
}

cleanup() {
  preserve_hermes_logs
  if [ -n "${GATEWAY_PID:-}" ]; then
    kill "$GATEWAY_PID" 2>/dev/null || true
    wait "$GATEWAY_PID" 2>/dev/null || true
  fi
  rm -rf "${FRESH_HOME:-}" 2>/dev/null || true
}
trap cleanup EXIT

if [ "$SWEEP_ADAPTER" = "openclaw" ]; then
  echo "Starting OpenClaw gateway on :18789 ..."
  HOME="$FRESH_HOME" \
  OPENCLAW_HOME="$FRESH_HOME" \
  OPENCLAW_STATE_DIR="$FRESH_STATE" \
  OPENCLAW_CONFIG_PATH="$FRESH_STATE/openclaw.json" \
  XDG_CONFIG_HOME="$FRESH_HOME/.config" \
    openclaw gateway run \
    --allow-unconfigured \
    --dev \
    --bind loopback \
    --port 18789 \
    --auth token \
    --token "$OPENCLAW_GATEWAY_TOKEN" \
    --compact \
    > "$GWLOG" 2>&1 &
  GATEWAY_PID=$!
  ready=0
  for i in $(seq 1 180); do
    if curl -sf -H "Authorization: Bearer $OPENCLAW_GATEWAY_TOKEN" http://127.0.0.1:18789/healthz > /dev/null 2>&1; then
      echo "Gateway healthy after ${i}s"
      ready=1
      break
    fi
    sleep 1
  done
  if [ "$ready" -ne 1 ]; then
    echo "ERROR: gateway failed to become healthy"
    tail -80 "$GWLOG" 2>/dev/null || true
    exit 1
  fi
  # OpenClaw's dev gateway normalizes state during startup and may rewrite
  # exec approval defaults. Reassert the eval-local approval socket after boot.
  write_eval_exec_approvals || exit 1
  if [ -r "/proc/$GATEWAY_PID/environ" ]; then
    actual_home="$(tr '\0' '\n' < "/proc/$GATEWAY_PID/environ" | awk -F= '$1 == "HOME" { print $2; exit }')"
    if [ "$actual_home" != "$FRESH_HOME" ]; then
      echo "ERROR: gateway HOME escaped container eval home: ${actual_home:-<unset>} != $FRESH_HOME"
      tail -120 "$GWLOG" 2>/dev/null || true
      exit 1
    fi
  fi
  if [ ! -f "$FRESH_STATE/exec-approvals.json" ] || grep -q '/home/node/.openclaw' "$FRESH_STATE/exec-approvals.json"; then
    echo "ERROR: exec approvals are not isolated in $FRESH_STATE"
    exit 1
  fi
  echo "Waiting for OpenClaw session control plane ..."
  python - <<'PY'
import asyncio
import os
import sys
import time

from clawbench.client import GatewayClient, GatewayConfig


async def probe_once(attempt: int) -> None:
    config = GatewayConfig(
        url=os.environ["OPENCLAW_GATEWAY_URL"],
        token=os.environ["OPENCLAW_GATEWAY_TOKEN"],
        connect_timeout=30.0,
        request_timeout=30.0,
    )
    async with GatewayClient(config) as client:
        key = await client.create_session(
            model=os.environ["SWEEP_MODEL"],
            label=f"clawbench-readiness-probe-{os.getpid()}-{attempt}",
        )
        await client.delete_session(key)


async def main() -> int:
    deadline = time.monotonic() + 240
    attempt = 0
    last_error = ""
    while time.monotonic() < deadline:
        attempt += 1
        try:
            await probe_once(attempt)
            print(f"Gateway session control plane ready after {attempt} attempt(s)")
            return 0
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"Gateway control probe {attempt} not ready: {last_error}")
            await asyncio.sleep(5)
    print(f"ERROR: gateway session control plane did not become ready: {last_error}", file=sys.stderr)
    return 1


raise SystemExit(asyncio.run(main()))
PY
  if [ "$?" -ne 0 ]; then
    tail -120 "$GWLOG" 2>/dev/null || true
    exit 1
  fi
fi

TASK_ARGS=()
if [ -n "${CHERRY_TASKS:-}" ]; then
  IFS=',' read -ra TASK_ARR <<< "$CHERRY_TASKS"
  for task_id in "${TASK_ARR[@]}"; do
    TASK_ARGS+=("--task" "$task_id")
  done
fi

clawbench run \
  --adapter "$SWEEP_ADAPTER" \
  --model "$SWEEP_MODEL" \
  --runs "$SWEEP_RUNS" \
  --concurrency "$SWEEP_CONCURRENCY" \
  --browser-concurrency "$SWEEP_BROWSER_CONCURRENCY" \
  --no-randomize \
  "${TASK_ARGS[@]}" \
  --output "$OUT" \
  > "$LOG" 2>&1
status=$?
preserve_hermes_logs

echo "===== clawbench exit=$status $(date '+%Y-%m-%d %H:%M:%S') ====="
tail -80 "$LOG" 2>/dev/null || true

exit "$status"
