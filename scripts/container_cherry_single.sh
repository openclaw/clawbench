#!/bin/bash
# Cherry-pick variant of container_sweep_single.sh: runs ONLY the tasks listed
# in $CHERRY_TASKS (comma-separated task IDs), with state-dir isolation.
#
# Required env vars:
#   SWEEP_LABEL   (e.g. opus47)
#   SWEEP_MODEL   (e.g. anthropic/claude-opus-4-7)
#   SWEEP_PROFILE (absolute path in container)
#   SWEEP_LOGDIR  (default /data/drift_2026-04-20-cherry)
#   SWEEP_OUT_TAG (default v2026-4-20-cherry)
#   CHERRY_TASKS  (comma-separated task IDs, e.g. "t2-ctx-pronoun-resolve,t3-fin-budget-monthly")

set -u

: "${SWEEP_LABEL:?SWEEP_LABEL required}"
: "${SWEEP_MODEL:?SWEEP_MODEL required}"
: "${SWEEP_PROFILE:?SWEEP_PROFILE required}"
: "${CHERRY_TASKS:?CHERRY_TASKS required (comma-separated task IDs)}"

: "${SWEEP_LOGDIR:=/data/drift_2026-04-20-cherry}"
: "${SWEEP_OUT_TAG:=v2026-4-20-cherry}"

cd /data

LOGDIR="$SWEEP_LOGDIR"
mkdir -p "$LOGDIR"

export OPENCLAW_GATEWAY_TOKEN="local-dev-token-for-testing"
export CLAWBENCH_RUN_CACHE_DIR="/data/run_cache"
mkdir -p "$CLAWBENCH_RUN_CACHE_DIR"
export NODE_OPTIONS="--max-old-space-size=4096"
# OpenClaw 4.22+ has slower agents.create / sessions.create on cold start
# (we observed 72s for opus-4-7). Bump RPC timeouts so the harness doesn't
# cancel mid-flight. Override defaults of 30s / 60s respectively.
export CLAWBENCH_CONNECT_TIMEOUT="${CLAWBENCH_CONNECT_TIMEOUT:-120}"
export CLAWBENCH_REQUEST_TIMEOUT="${CLAWBENCH_REQUEST_TIMEOUT:-300}"
export CLAWBENCH_PER_RUN_BUDGET_SECONDS="${CLAWBENCH_PER_RUN_BUDGET_SECONDS:-900}"
export HERMES_STEP_TIMEOUT_SECONDS="${HERMES_STEP_TIMEOUT_SECONDS:-180}"

# State-dir isolation (same as container_sweep_single.sh)
SRC_STATE="/home/node/.openclaw"
FRESH_STATE="/tmp/openclaw-state-${SWEEP_LABEL}-$$"
echo "[state-isolate] cloning config from $SRC_STATE to $FRESH_STATE"
mkdir -p "$FRESH_STATE"
[ -f "$SRC_STATE/openclaw.json" ] && cp "$SRC_STATE/openclaw.json" "$FRESH_STATE/openclaw.json"
[ -f "$SRC_STATE/exec-approvals.json" ] && cp "$SRC_STATE/exec-approvals.json" "$FRESH_STATE/exec-approvals.json"
for d in identity devices tasks subagents flows cron; do
  [ -d "$SRC_STATE/$d" ] && cp -r "$SRC_STATE/$d" "$FRESH_STATE/$d"
done
mkdir -p "$FRESH_STATE/agents" "$FRESH_STATE/workspace" "$FRESH_STATE/logs" "$FRESH_STATE/memory" "$FRESH_STATE/cache"
export OPENCLAW_STATE_DIR="$FRESH_STATE"
export OPENCLAW_CONFIG_PATH="$FRESH_STATE/openclaw.json"
echo "[state-isolate] OPENCLAW_STATE_DIR=$OPENCLAW_STATE_DIR"

python - <<'PY'
import json
import os
from pathlib import Path

cfg_path = Path(os.environ["OPENCLAW_CONFIG_PATH"])
data = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}

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

exec_host = os.environ.get("OPENCLAW_EXEC_HOST", "gateway").strip().lower()
if exec_host not in {"auto", "gateway", "sandbox", "node"}:
    raise SystemExit(f"invalid OPENCLAW_EXEC_HOST={exec_host!r}")

set_nested(data, "tools.exec.host", exec_host)
set_nested(data, "tools.exec.security", "full")
set_nested(data, "tools.exec.ask", "off")
set_nested(data, "approvals.exec.enabled", False)
cfg_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

approvals_path = cfg_path.with_name("exec-approvals.json")
approvals = {
    "version": 1,
    "socket": {
        "path": str(approvals_path.with_suffix(".sock")),
        "token": "container-cherry-eval-token",
    },
    "defaults": {"security": "full", "ask": "off", "askFallback": "full"},
    "agents": {"*": {"security": "full", "ask": "off", "askFallback": "full"}},
}
approvals_path.write_text(json.dumps(approvals, indent=2) + "\n", encoding="utf-8")
PY

# Map model to cache subdir (for archiving)
case "$SWEEP_MODEL" in
  anthropic/claude-opus-4-7)        CACHE_SUB="anthropic_claude-opus-4-7" ;;
  anthropic/claude-opus-4-6)        CACHE_SUB="anthropic_claude-opus-4-6" ;;
  anthropic/claude-sonnet-4-6)      CACHE_SUB="anthropic_claude-sonnet-4-6" ;;
  openai/gpt-5.5)                   CACHE_SUB="openai_gpt-5.5" ;;
  openai/gpt-5.4)                   CACHE_SUB="openai_gpt-5.4" ;;
  google/gemini-3.1-pro-preview)    CACHE_SUB="google_gemini-3.1-pro-preview" ;;
  openrouter/z-ai/glm-5.1)          CACHE_SUB="openrouter_z-ai_glm-5.1" ;;
  openrouter/qwen/qwen3.6-plus)     CACHE_SUB="openrouter_qwen_qwen3.6-plus" ;;
  openrouter/minimax/minimax-m2.7)  CACHE_SUB="openrouter_minimax_minimax-m2.7" ;;
  openrouter/moonshotai/kimi-k2.6)  CACHE_SUB="openrouter_moonshotai_kimi-k2.6" ;;
  openrouter/moonshotai/kimi-k2.5)  CACHE_SUB="openrouter_moonshotai_kimi-k2.5" ;;
  openrouter/deepseek/deepseek-v4-pro) CACHE_SUB="openrouter_deepseek_deepseek-v4-pro" ;;
  deepseek/deepseek-v4-pro)         CACHE_SUB="deepseek_deepseek-v4-pro" ;;
  deepseek/v4-pro)                  CACHE_SUB="deepseek_v4-pro" ;;
  *) CACHE_SUB="" ;;
esac

OUT="$LOGDIR/docker_${SWEEP_LABEL}_${SWEEP_OUT_TAG}.json"
LOG="$LOGDIR/docker_${SWEEP_LABEL}_${SWEEP_OUT_TAG}.log"
GWLOG="$LOGDIR/gateway_${SWEEP_LABEL}.log"

echo "===== CHERRY-PICK SWEEP $(date '+%Y-%m-%d %H:%M:%S') ====="
echo "label:   $SWEEP_LABEL"
echo "model:   $SWEEP_MODEL"
echo "tasks:   $CHERRY_TASKS"
echo "out:     $OUT"

# Force-clear this model's run_cache (including fixed-task slots — so they
# actually re-run against the new image instead of hitting old cache).
if [ -n "$CACHE_SUB" ] && [ -d "$CLAWBENCH_RUN_CACHE_DIR/$CACHE_SUB" ]; then
  echo "clearing cache: $CLAWBENCH_RUN_CACHE_DIR/$CACHE_SUB"
  rm -rf "$CLAWBENCH_RUN_CACHE_DIR/$CACHE_SUB"
fi
[ -f "$OUT" ] && rm -f "$OUT"

# Start gateway with bumped heap
echo "Starting gateway on :18789 (heap=4GB) ..."
openclaw gateway --port 18789 > "$GWLOG" 2>&1 &
GATEWAY_PID=$!
ready=0
for i in $(seq 1 120); do
  if curl -sf -H "Authorization: Bearer $OPENCLAW_GATEWAY_TOKEN" http://127.0.0.1:18789/ready > /dev/null 2>&1; then
    echo "Gateway ready after ${i}s"
    ready=1
    break
  fi
  sleep 1
done
if [ $ready -ne 1 ]; then
  echo "ERROR: gateway failed to become ready within 120s"
  tail -30 "$GWLOG"
  exit 1
fi

# Build -t args from comma-separated list
TASK_ARGS=()
IFS=',' read -ra TASK_ARR <<< "$CHERRY_TASKS"
for t in "${TASK_ARR[@]}"; do
  TASK_ARGS+=("-t" "$t")
done

echo "===== $(date '+%H:%M:%S') running clawbench with tasks: ${TASK_ARR[*]} ====="
# NOTE: --profile intentionally OMITTED. The legacy frontier_*.yaml profile
# format is incompatible with OpenClaw 4.22+ (loads n_tools_total=0,
# starves the agent of tools, all runs fail with environment_unavailable
# or timeout). Running with the default openclaw tool stack — same for
# all models, so the comparison stays apples-to-apples.
PROFILE_ARG=""
if [ -n "${USE_PROFILE:-}" ] && [ -f "$SWEEP_PROFILE" ]; then
  PROFILE_ARG="--profile $SWEEP_PROFILE"
fi
clawbench run \
  --model "$SWEEP_MODEL" \
  --runs 3 \
  --concurrency "${CLAWBENCH_CONCURRENCY:-1}" \
  $PROFILE_ARG \
  --judge-model "anthropic/claude-sonnet-4-6" \
  "${TASK_ARGS[@]}" \
  -o "$OUT" \
  > "$LOG" 2>&1
status=$?

if [ $status -eq 0 ]; then
  echo "===== $(date '+%H:%M:%S') done $SWEEP_LABEL (exit 0) ====="
else
  echo "===== $(date '+%H:%M:%S') FAILED $SWEEP_LABEL (exit $status) ====="
  tail -20 "$LOG"
fi

# Archive cache to v2026-4-20-cherry tag
# shellcheck disable=SC1091
source "$(dirname "$0")/_archive_cache.sh" 2>/dev/null && archive_run_cache || echo "[archive] helper missing"

kill $GATEWAY_PID 2>/dev/null
wait $GATEWAY_PID 2>/dev/null

# Clean up isolated state dir
[ -n "${FRESH_STATE:-}" ] && [ -d "$FRESH_STATE" ] && rm -rf "$FRESH_STATE"

exit $status
