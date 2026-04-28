#!/bin/bash
# Single-model sweep with fresh gateway + bumped Node heap to prevent OOM.
#
# Invocation (from host):
#   docker run -d --name clawbench-sweep-<LABEL> \
#     -e SWEEP_LABEL=<label> -e SWEEP_MODEL=<routed-model> -e SWEEP_PROFILE=<abs-profile-path> \
#     -v .../scripts:/home/node/app/scripts:ro \
#     -v .../data:/data \
#     -v .../data/container-home-openclaw:/home/node/.openclaw \
#     -v .../profiles:/home/node/app/profiles:ro \
#     --memory 8g \
#     clawbench-clawbench:latest \
#     bash /home/node/app/scripts/container_sweep_single.sh
#
# Differences vs container_sweep.sh:
# - Bumps gateway Node.js heap via NODE_OPTIONS=--max-old-space-size=4096 (prevents 2GB OOM we saw at ~4h)
# - One model per container (no shared-gateway drift between models)
# - Force-clears run_cache for THIS model before running (prevents cache-replay masking)
# - Writes to the same $LOGDIR/docker_${label}_${SWEEP_OUT_TAG}.json as the original sweep
#   so generate_drift_report.py picks it up without changes

set -u

: "${SWEEP_LABEL:?SWEEP_LABEL required (e.g. glm, minimax, kimi)}"
: "${SWEEP_MODEL:?SWEEP_MODEL required (e.g. openrouter/z-ai/glm-5.1)}"
: "${SWEEP_PROFILE:?SWEEP_PROFILE required (absolute path in container)}"

# Optional overrides (defaults target the v4.14 drift sweep):
#   SWEEP_LOGDIR — where JSONs and logs go (default /data/drift_2026-04-14)
#   SWEEP_OUT_TAG — tag embedded in output filename (default v2026-4-14)
: "${SWEEP_LOGDIR:=/data/drift_2026-04-14}"
: "${SWEEP_OUT_TAG:=v2026-4-14}"

cd /data

LOGDIR="$SWEEP_LOGDIR"
mkdir -p "$LOGDIR"

export OPENCLAW_GATEWAY_TOKEN="local-dev-token-for-testing"
export CLAWBENCH_RUN_CACHE_DIR="/data/run_cache"
mkdir -p "$CLAWBENCH_RUN_CACHE_DIR"

# OOM fix: give the gateway Node process a 4GB old-space ceiling instead of the default ~2GB.
# Scoped via env so we don't stomp on other Node processes (clawbench itself is python).
export NODE_OPTIONS="--max-old-space-size=4096"

# State-dir isolation: the shared /home/node/.openclaw mount accumulates cruft
# across sweeps (agents/, workspace/, logs/, memory/, stale openclaw.json.*.tmp)
# which triggers gateway hot-reload churn and cascading `RPC agents.create timed
# out after 60s` failures. Give each sweep a pristine state dir that carries
# over only the config (openclaw.json, identity/, devices/, exec-approvals.json,
# tasks/, subagents/, flows/, cron/) and leaves runtime state empty.
SRC_STATE="/home/node/.openclaw"
FRESH_STATE="/tmp/openclaw-state-${SWEEP_LABEL}-$$"
echo "[state-isolate] cloning config from $SRC_STATE to $FRESH_STATE"
mkdir -p "$FRESH_STATE"
# Copy the main config (skip the .tmp/.bak/.clobbered/.pre-* cruft that can
# confuse the loader — only the canonical openclaw.json is needed).
if [ -f "$SRC_STATE/openclaw.json" ]; then
  cp "$SRC_STATE/openclaw.json" "$FRESH_STATE/openclaw.json"
fi
if [ -f "$SRC_STATE/exec-approvals.json" ]; then
  cp "$SRC_STATE/exec-approvals.json" "$FRESH_STATE/exec-approvals.json"
fi
# Carry over static config dirs — these are read-mostly and don't accumulate
# per-run cruft. SKIP: agents/ workspace*/ logs/ memory/ cache/ browser/ canvas/
# which all grow unboundedly across sweeps.
for d in identity devices tasks subagents flows cron; do
  if [ -d "$SRC_STATE/$d" ]; then
    cp -r "$SRC_STATE/$d" "$FRESH_STATE/$d"
  fi
done
# Ensure runtime dirs exist but are empty
mkdir -p "$FRESH_STATE/agents" "$FRESH_STATE/workspace" "$FRESH_STATE/logs" "$FRESH_STATE/memory" "$FRESH_STATE/cache"
export OPENCLAW_STATE_DIR="$FRESH_STATE"
echo "[state-isolate] OPENCLAW_STATE_DIR=$OPENCLAW_STATE_DIR"
du -sh "$FRESH_STATE" 2>/dev/null | sed 's/^/[state-isolate] size: /'

# Map label -> cache subdir (matches what clawbench writes)
case "$SWEEP_MODEL" in
  anthropic/claude-opus-4-7)        CACHE_SUB="anthropic_claude-opus-4-7" ;;
  anthropic/claude-sonnet-4-7)      CACHE_SUB="anthropic_claude-sonnet-4-7" ;;
  anthropic/claude-opus-4-6)        CACHE_SUB="anthropic_claude-opus-4-6" ;;
  anthropic/claude-sonnet-4-6)      CACHE_SUB="anthropic_claude-sonnet-4-6" ;;
  openai/gpt-5.4)                   CACHE_SUB="openai_gpt-5.4" ;;
  openai/gpt-5.2)                   CACHE_SUB="openai_gpt-5.2" ;;
  google/gemini-3.1-pro-preview)    CACHE_SUB="google_gemini-3.1-pro-preview" ;;
  openrouter/z-ai/glm-5.1)          CACHE_SUB="openrouter_z-ai_glm-5.1" ;;
  openrouter/qwen/qwen3.6-plus)     CACHE_SUB="openrouter_qwen_qwen3.6-plus" ;;
  openrouter/minimax/minimax-m2.7)  CACHE_SUB="openrouter_minimax_minimax-m2.7" ;;
  openrouter/moonshotai/kimi-k2.5)  CACHE_SUB="openrouter_moonshotai_kimi-k2.5" ;;
  # kimi-k2.6 is not yet supported in the openclaw version under test — skip.
  *) CACHE_SUB="" ;;
esac

OUT="$LOGDIR/docker_${SWEEP_LABEL}_${SWEEP_OUT_TAG}.json"
LOG="$LOGDIR/docker_${SWEEP_LABEL}_${SWEEP_OUT_TAG}.log"
GWLOG="$LOGDIR/gateway_${SWEEP_LABEL}.log"

echo "===== SINGLE-MODEL SWEEP START $(date '+%Y-%m-%d %H:%M:%S') ====="
echo "label:   $SWEEP_LABEL"
echo "model:   $SWEEP_MODEL"
echo "profile: $SWEEP_PROFILE"
echo "out:     $OUT"
echo "gwlog:   $GWLOG"
echo "NODE_OPTIONS: $NODE_OPTIONS"

# Force-clear this model's run_cache so we actually re-run (no replays)
if [ -n "$CACHE_SUB" ] && [ -d "$CLAWBENCH_RUN_CACHE_DIR/$CACHE_SUB" ]; then
  echo "clearing cache: $CLAWBENCH_RUN_CACHE_DIR/$CACHE_SUB"
  rm -rf "$CLAWBENCH_RUN_CACHE_DIR/$CACHE_SUB"
fi

# Also remove any stale result JSON so we don't skip-on-idempotence
if [ -f "$OUT" ]; then
  echo "removing stale result: $OUT"
  rm -f "$OUT"
fi

# Start gateway with bumped heap
echo "Starting gateway on :18789 (heap=4GB) ..."
openclaw gateway --port 18789 > "$GWLOG" 2>&1 &
GATEWAY_PID=$!
echo "gateway pid=$GATEWAY_PID"

ready=0
for i in $(seq 1 120); do
  if curl -sf -H "Authorization: Bearer $OPENCLAW_GATEWAY_TOKEN" http://127.0.0.1:18789/health > /dev/null 2>&1; then
    echo "Gateway healthy after ${i}s"
    ready=1
    break
  fi
  sleep 1
done
if [ $ready -ne 1 ]; then
  echo "ERROR: gateway failed to come up within 120s"
  tail -30 "$GWLOG"
  exit 1
fi

echo "===== $(date '+%H:%M:%S') starting $SWEEP_LABEL ($SWEEP_MODEL) ====="
clawbench run \
  --model "$SWEEP_MODEL" \
  --runs 3 \
  --concurrency 4 \
  --profile "$SWEEP_PROFILE" \
  --judge-model "anthropic/claude-sonnet-4-6" \
  -o "$OUT" \
  > "$LOG" 2>&1
status=$?

if [ $status -eq 0 ]; then
  echo "===== $(date '+%H:%M:%S') done $SWEEP_LABEL (exit 0) ====="
else
  echo "===== $(date '+%H:%M:%S') FAILED $SWEEP_LABEL (exit $status) ====="
  tail -20 "$LOG"
fi

# Archive the cache for future audits (preserves transcripts per sweep tag)
# shellcheck disable=SC1091
source "$(dirname "$0")/_archive_cache.sh" 2>/dev/null && archive_run_cache || echo "[archive] helper missing, skipping"

echo ""
echo "===== SINGLE-MODEL SWEEP END $(date '+%Y-%m-%d %H:%M:%S') ====="
kill $GATEWAY_PID 2>/dev/null
wait $GATEWAY_PID 2>/dev/null
echo "gateway stopped"

# Clean up the isolated state dir (don't accumulate /tmp cruft across sweeps).
if [ -n "${FRESH_STATE:-}" ] && [ -d "$FRESH_STATE" ]; then
  echo "[state-isolate] removing $FRESH_STATE"
  rm -rf "$FRESH_STATE"
fi

exit $status
