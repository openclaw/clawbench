#!/bin/bash
# Container sweep: all 7 models against 2026.4.14 inside the clawbench image.
# Expected to run as: docker run -d ... clawbench-clawbench:latest bash /home/node/app/scripts/container_sweep.sh
# Writes results to /data/drift_2026-04-14/ (host: ./data/drift_2026-04-14/).

set -u
# /home/node/app is root-owned; cd to writable /data instead.
# Profiles live at /home/node/app/profiles (read-only bind mount), reference by absolute path.
cd /data
PROFILE_DIR="/home/node/app/profiles"

LOGDIR="/data/drift_2026-04-14"
mkdir -p "$LOGDIR"

export OPENCLAW_GATEWAY_TOKEN="local-dev-token-for-testing"
# Cache dir inside container, backed by ./data volume
export CLAWBENCH_RUN_CACHE_DIR="/data/run_cache"
mkdir -p "$CLAWBENCH_RUN_CACHE_DIR"

echo "===== CONTAINER SWEEP START $(date '+%Y-%m-%d %H:%M:%S') ====="
echo "openclaw: $(openclaw --version 2>&1)"
echo "clawbench: $(clawbench --version 2>&1 || true)"
echo "logdir: $LOGDIR"
echo "cache:  $CLAWBENCH_RUN_CACHE_DIR"
echo ""

# Start gateway in background
echo "Starting gateway on :18789..."
openclaw gateway --port 18789 > "$LOGDIR/gateway.log" 2>&1 &
GATEWAY_PID=$!
echo "gateway pid=$GATEWAY_PID"

# Wait for gateway health (up to 120s)
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
  tail -30 "$LOGDIR/gateway.log"
  exit 1
fi

echo ""

# model, profile, label
declare -a runs=(
  "anthropic/claude-opus-4-6|$PROFILE_DIR/frontier_opus_4_6.yaml|opus"
  "anthropic/claude-sonnet-4-6|$PROFILE_DIR/frontier_sonnet_4_6.yaml|sonnet"
  "openai/gpt-5.4|$PROFILE_DIR/frontier_gpt_5_4.yaml|gpt54"
  "openai/gpt-5.2|$PROFILE_DIR/frontier_gpt_5_2.yaml|gpt52"
  "openrouter/z-ai/glm-5.1|$PROFILE_DIR/frontier_glm_5_1.yaml|glm"
  "openrouter/minimax/minimax-m2.7|$PROFILE_DIR/frontier_minimax_m27.yaml|minimax"
  "openrouter/moonshotai/kimi-k2.5|$PROFILE_DIR/frontier_kimi_k25.yaml|kimi"
)

for entry in "${runs[@]}"; do
  IFS='|' read -r model profile label <<< "$entry"
  out="$LOGDIR/docker_${label}_v2026-4-14.json"
  log="$LOGDIR/docker_${label}_v2026-4-14.log"

  # Skip if complete result already exists (idempotent restart)
  if [ -f "$out" ] && python3 -c "import json,sys; r=json.load(open('$out')); sys.exit(0 if r.get('overall_score') is not None else 1)" 2>/dev/null; then
    echo "===== $(date '+%H:%M:%S') skip $label (already complete: $out) ====="
    continue
  fi

  echo "===== $(date '+%H:%M:%S') starting $label ($model) ====="
  clawbench run \
    --model "$model" \
    --runs 3 \
    --concurrency 4 \
    --profile "$profile" \
    --judge-model "anthropic/claude-sonnet-4-6" \
    -o "$out" \
    > "$log" 2>&1
  status=$?
  if [ $status -eq 0 ]; then
    echo "===== $(date '+%H:%M:%S') done $label (exit 0) ====="
  else
    echo "===== $(date '+%H:%M:%S') FAILED $label (exit $status) ====="
    tail -20 "$log"
  fi
done

echo ""
echo "===== CONTAINER SWEEP END $(date '+%Y-%m-%d %H:%M:%S') ====="
kill $GATEWAY_PID 2>/dev/null
wait $GATEWAY_PID 2>/dev/null
echo "gateway stopped"
