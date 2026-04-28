#!/bin/bash
# Minimal single-model sweep — 1 run per task (not 3) for fast validation.
# Used to quickly test if an openrouter-stream fix actually works without
# committing to a full 60-minute 3-run sweep.
#
# Invocation (from host):
#   docker run -d --name clawbench-<LABEL> \
#     -e SWEEP_LABEL=<label> -e SWEEP_MODEL=<routed-model> \
#     -e SWEEP_PROFILE=<abs-profile-path> \
#     -e SWEEP_LOGDIR=<output-dir-in-container> \
#     -e SWEEP_OUT_TAG=<tag> \
#     -v .../scripts:/home/node/app/scripts:ro \
#     -v .../data:/data \
#     -v .../data/container-home-openclaw:/home/node/.openclaw \
#     -v .../profiles:/home/node/app/profiles:ro \
#     --memory 8g \
#     <image> \
#     bash /home/node/app/scripts/container_sweep_minimal.sh

set -u

: "${SWEEP_LABEL:?SWEEP_LABEL required}"
: "${SWEEP_MODEL:?SWEEP_MODEL required}"
: "${SWEEP_PROFILE:?SWEEP_PROFILE required}"
: "${SWEEP_LOGDIR:?SWEEP_LOGDIR required}"
: "${SWEEP_OUT_TAG:?SWEEP_OUT_TAG required}"

cd /data
mkdir -p "$SWEEP_LOGDIR"

export OPENCLAW_GATEWAY_TOKEN="local-dev-token-for-testing"
export CLAWBENCH_RUN_CACHE_DIR="/data/run_cache"
mkdir -p "$CLAWBENCH_RUN_CACHE_DIR"
export NODE_OPTIONS="--max-old-space-size=4096"

# Clear cache for target model
case "$SWEEP_MODEL" in
  openrouter/z-ai/glm-5.1)          CACHE_SUB="openrouter_z-ai_glm-5.1" ;;
  openrouter/minimax/minimax-m2.7)  CACHE_SUB="openrouter_minimax_minimax-m2.7" ;;
  openrouter/moonshotai/kimi-k2.5)  CACHE_SUB="openrouter_moonshotai_kimi-k2.5" ;;
  *) CACHE_SUB="" ;;
esac
if [ -n "$CACHE_SUB" ] && [ -d "$CLAWBENCH_RUN_CACHE_DIR/$CACHE_SUB" ]; then
  echo "clearing cache: $CLAWBENCH_RUN_CACHE_DIR/$CACHE_SUB"
  rm -rf "$CLAWBENCH_RUN_CACHE_DIR/$CACHE_SUB"
fi

OUT="$SWEEP_LOGDIR/docker_${SWEEP_LABEL}_${SWEEP_OUT_TAG}.json"
LOG="$SWEEP_LOGDIR/docker_${SWEEP_LABEL}_${SWEEP_OUT_TAG}.log"
GWLOG="$SWEEP_LOGDIR/gateway_${SWEEP_LABEL}.log"

rm -f "$OUT"

echo "===== MINIMAL SWEEP START $(date '+%Y-%m-%d %H:%M:%S') ====="
echo "label:   $SWEEP_LABEL"
echo "model:   $SWEEP_MODEL"
echo "profile: $SWEEP_PROFILE"
echo "out:     $OUT"
echo "runs:    1 per task (MINIMAL)"

echo "Starting gateway on :18789 (heap=4GB) ..."
openclaw gateway --port 18789 > "$GWLOG" 2>&1 &
GATEWAY_PID=$!

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
  echo "ERROR: gateway failed to come up"
  exit 1
fi

echo "===== $(date '+%H:%M:%S') starting $SWEEP_LABEL ($SWEEP_MODEL) ====="
clawbench run \
  --model "$SWEEP_MODEL" \
  --runs 1 \
  --concurrency 4 \
  --profile "$SWEEP_PROFILE" \
  --judge-model "anthropic/claude-sonnet-4-6" \
  -o "$OUT" \
  > "$LOG" 2>&1
status=$?

echo "===== $(date '+%H:%M:%S') done $SWEEP_LABEL (exit $status) ====="

# Archive the cache for future audits
# shellcheck disable=SC1091
source "$(dirname "$0")/_archive_cache.sh" 2>/dev/null && archive_run_cache || echo "[archive] helper missing, skipping"

kill $GATEWAY_PID 2>/dev/null
wait $GATEWAY_PID 2>/dev/null
exit $status
