#!/bin/bash
# Resume drift sweep — opus already finished (rerun_opus_v2026-4-14.json kept).
# Runs the remaining 6 models against host gateway 2026.4.14.
# Designed to survive terminal close: launched via nohup, sets safe cache dir.

set -u
cd /Users/zhentongfan/Desktop/openclaw/clawbench
source .venv/bin/activate
export OPENCLAW_GATEWAY_TOKEN="local-dev-token-for-testing"
# Use host-writable cache dir (default /data/run_cache only exists in container)
export CLAWBENCH_RUN_CACHE_DIR="/Users/zhentongfan/Desktop/openclaw/clawbench/data/run_cache"

mkdir -p results/drift_2026-04-14
LOGDIR="results/drift_2026-04-14"

# Skipping opus — rerun_opus_v2026-4-14.json already complete (overall=0.536).
# model, profile, label
declare -a runs=(
  "anthropic/claude-sonnet-4-6|profiles/frontier_sonnet_4_6.yaml|sonnet"
  "openai/gpt-5.4|profiles/frontier_gpt_5_4.yaml|gpt54"
  "openai/gpt-5.2|profiles/frontier_gpt_5_2.yaml|gpt52"
  "openrouter/z-ai/glm-5.1|profiles/frontier_glm_5_1.yaml|glm"
  "openrouter/minimax/minimax-m2.7|profiles/frontier_minimax_m27.yaml|minimax"
  "openrouter/moonshotai/kimi-k2.5|profiles/frontier_kimi_k25.yaml|kimi"
)

echo "===== SWEEP START $(date '+%Y-%m-%d %H:%M:%S') ====="
echo "cache dir: $CLAWBENCH_RUN_CACHE_DIR"
echo "logdir:    $LOGDIR"
echo ""

for entry in "${runs[@]}"; do
  IFS='|' read -r model profile label <<< "$entry"
  out="$LOGDIR/rerun_${label}_v2026-4-14.json"
  log="$LOGDIR/rerun_${label}_v2026-4-14.log"

  # Skip if a complete result already exists (idempotent restart)
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
  fi
done

echo ""
echo "===== SWEEP END $(date '+%Y-%m-%d %H:%M:%S') ====="
