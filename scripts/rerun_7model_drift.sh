#!/bin/bash
# Re-run all 7 models against OpenClaw 2026.4.14 to measure version drift vs the 2026.4.8 baseline.
# Uses same 3 runs, concurrency 4 config as the original sweep.

set -u
cd /Users/zhentongfan/Desktop/openclaw/clawbench
source .venv/bin/activate
export OPENCLAW_GATEWAY_TOKEN="local-dev-token-for-testing"

mkdir -p results/drift_2026-04-14
LOGDIR="results/drift_2026-04-14"

# model, profile, label
declare -a runs=(
  "anthropic/claude-opus-4-6|profiles/frontier_opus_4_6.yaml|opus"
  "anthropic/claude-sonnet-4-6|profiles/frontier_sonnet_4_6.yaml|sonnet"
  "openai/gpt-5.4|profiles/frontier_gpt_5_4.yaml|gpt54"
  "openai/gpt-5.2|profiles/frontier_gpt_5_2.yaml|gpt52"
  "openrouter/z-ai/glm-5.1|profiles/frontier_glm_5_1.yaml|glm"
  "openrouter/minimax/minimax-m2.7|profiles/frontier_minimax_m27.yaml|minimax"
  "openrouter/moonshotai/kimi-k2.5|profiles/frontier_kimi_k25.yaml|kimi"
)

for entry in "${runs[@]}"; do
  IFS='|' read -r model profile label <<< "$entry"
  out="$LOGDIR/rerun_${label}_v2026-4-14.json"
  log="$LOGDIR/rerun_${label}_v2026-4-14.log"
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

echo "===== ALL DONE $(date '+%H:%M:%S') ====="
