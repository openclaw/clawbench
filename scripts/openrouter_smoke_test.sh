#!/bin/bash
# OpenRouter-adapter smoke test: does v4.14's createOpenRouterWrapper break
# for ALL routes, or only for non-OpenAI/Anthropic upstream slugs?
#
# Strategy: run 1 easy task × 1 run for each of two test models that both go
# through createOpenRouterWrapper but target different upstream provider
# families:
#   - openrouter/openai/gpt-5.4      (OpenAI-flavored upstream)
#   - openrouter/anthropic/claude-sonnet-4-6  (Anthropic-flavored upstream)
#
# Compare against the known-broken openrouter/z-ai/glm-5.1 (non-OpenAI/non-Anthropic upstream).
#
# Runs as:
#   docker run --rm --name clawbench-smoke \
#     -v .../scripts:/home/node/app/scripts:ro \
#     -v .../data:/data \
#     -v .../profiles:/home/node/app/profiles:ro \
#     --memory 4g \
#     clawbench-clawbench:latest \
#     bash /home/node/app/scripts/openrouter_smoke_test.sh

set -u
cd /data

LOGDIR="/data/drift_2026-04-14/smoke_test"
mkdir -p "$LOGDIR"

export OPENCLAW_GATEWAY_TOKEN="local-dev-token-for-testing"
export CLAWBENCH_RUN_CACHE_DIR="/data/smoke_run_cache"
# Fresh cache so nothing replays
rm -rf "$CLAWBENCH_RUN_CACHE_DIR"
mkdir -p "$CLAWBENCH_RUN_CACHE_DIR"

export NODE_OPTIONS="--max-old-space-size=4096"

PROFILE_DIR="/home/node/app/profiles"
# Canary task — short, single file, fast; in the v4.14 sweep it hit 0.892 on
# working models and 0.340 (constant-default) on broken OpenRouter models.
TASK="t1-cal-quick-reminder"

echo "===== OPENROUTER ADAPTER SMOKE TEST $(date '+%Y-%m-%d %H:%M:%S') ====="
echo "task: $TASK"
echo "logdir: $LOGDIR"

GWLOG="$LOGDIR/gateway.log"
echo "starting gateway ..."
openclaw gateway --port 18789 > "$GWLOG" 2>&1 &
GATEWAY_PID=$!

ready=0
for i in $(seq 1 120); do
  if curl -sf -H "Authorization: Bearer $OPENCLAW_GATEWAY_TOKEN" http://127.0.0.1:18789/health > /dev/null 2>&1; then
    echo "gateway healthy after ${i}s"
    ready=1
    break
  fi
  sleep 1
done
if [ $ready -ne 1 ]; then
  echo "ERROR: gateway failed to come up"
  tail -30 "$GWLOG"
  kill $GATEWAY_PID 2>/dev/null
  exit 1
fi

# Each entry: model | profile (any compatible) | label
declare -a runs=(
  "openrouter/openai/gpt-5.4|$PROFILE_DIR/frontier_gpt_5_4.yaml|or_openai"
  "openrouter/anthropic/claude-sonnet-4-6|$PROFILE_DIR/frontier_sonnet_4_6.yaml|or_anthropic"
  "openrouter/z-ai/glm-5.1|$PROFILE_DIR/frontier_glm_5_1.yaml|or_zai_ctrl"
)

for entry in "${runs[@]}"; do
  IFS='|' read -r model profile label <<< "$entry"
  out="$LOGDIR/smoke_${label}.json"
  log="$LOGDIR/smoke_${label}.log"

  echo ""
  echo "===== $(date '+%H:%M:%S') smoke $label ($model) ====="
  # Capture only the incomplete-turn events emitted during this model's window
  gwlog_start_line=$(wc -l < "$GWLOG")

  clawbench run \
    --model "$model" \
    --runs 1 \
    --concurrency 1 \
    --task "$TASK" \
    --profile "$profile" \
    -o "$out" \
    > "$log" 2>&1
  status=$?

  # Count incomplete-turn events during this model's run
  inc_turns=$(tail -n +${gwlog_start_line} "$GWLOG" | grep -c "incomplete turn detected" || true)

  # Pull overall_score and tokens from the result JSON if present
  if [ -f "$out" ]; then
    score=$(python3 -c "import json; r=json.load(open('$out')); print(f\"{r.get('overall_score', 'null'):.4f}\" if isinstance(r.get('overall_score'), (int,float)) else 'null')" 2>/dev/null || echo "parse-err")
    tokens=$(python3 -c "import json; r=json.load(open('$out')); tpr=r.get('aggregates',{}).get('tokens_per_run_mean'); print(int(tpr) if tpr else 0)" 2>/dev/null || echo "parse-err")
    vskip=$(python3 -c "import json; r=json.load(open('$out')); print(sum(1 for t in r.get('tasks',[]) for run in t.get('runs',[]) if run.get('failure_mode')=='verification_skipped'))" 2>/dev/null || echo "parse-err")
  else
    score="no-output"; tokens="no-output"; vskip="no-output"
  fi

  echo "result $label: exit=$status overall=$score tokens=$tokens vskip=$vskip inc_turns=$inc_turns"
done

echo ""
echo "===== $(date '+%H:%M:%S') smoke test done ====="
kill $GATEWAY_PID 2>/dev/null
wait $GATEWAY_PID 2>/dev/null
echo "gateway stopped"
