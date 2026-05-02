#!/bin/bash
# Smoke-test all 10 models against a single fast task to verify the slug
# resolves, the API key is present, and the model produces output. Runs all
# 10 in parallel against OpenClaw 2026.4.26 image.
#
# Output: data/smoke_10model/docker_<label>_smoke.json + gateway logs
#         + summary at the end showing PASS/FAIL/TIMEOUT per model.

set -u

ROOT="/Users/zhentongfan/Desktop/openclaw/clawbench"
cd "$ROOT"

IMAGE="${IMAGE:-clawbench-clawbench:v2026-4-26-agent-hotfix}"
SMOKE_TASK="t1-fs-quick-note"   # short, deterministic, fast verifier
SWEEP_LOGDIR="/data/smoke_10model"
SWEEP_OUT_TAG="smoke"

declare -a models=(
  "anthropic/claude-opus-4-6|/home/node/app/profiles/frontier_opus_4_6.yaml|opus46"
  "anthropic/claude-opus-4-7|/home/node/app/profiles/frontier_opus_4_7.yaml|opus47"
  "anthropic/claude-sonnet-4-6|/home/node/app/profiles/frontier_sonnet_4_6.yaml|sonnet46"
  "openai/gpt-5.4|/home/node/app/profiles/frontier_gpt_5_4.yaml|gpt54"
  "openai/gpt-5.5|/home/node/app/profiles/frontier_gpt_5_5.yaml|gpt55"
  "google/gemini-3.1-pro-preview|/home/node/app/profiles/frontier_gemini_3_pro.yaml|gemini"
  "openrouter/deepseek/deepseek-v4-pro|/home/node/app/profiles/frontier_deepseek_v4.yaml|deepseek"
  "openrouter/z-ai/glm-5.1|/home/node/app/profiles/frontier_glm_5_1.yaml|glm"
  "openrouter/minimax/minimax-m2.7|/home/node/app/profiles/frontier_minimax_m27.yaml|minimax"
  "openrouter/moonshotai/kimi-k2.6|/home/node/app/profiles/frontier_kimi_k26.yaml|kimi26"
)

mkdir -p "$ROOT/data/smoke_10model"
# Clean previous smoke results
rm -f "$ROOT/data/smoke_10model/"*.json "$ROOT/data/smoke_10model/"*.log 2>/dev/null

echo "===== $(date '+%H:%M:%S') running 10 smoke containers SEQUENTIALLY ====="
echo "(parallel saturates API rate limits / disk I/O — sequential is the honest test)"
for entry in "${models[@]}"; do
  IFS='|' read -r model profile label <<< "$entry"
  name="clawbench-smoke-${label}"
  docker rm -f "$name" >/dev/null 2>&1 || true
  echo ""
  echo "  [$(date '+%H:%M:%S')] [$label] starting ($model)"
  docker run --rm --name "$name" \
    -e SWEEP_LABEL="$label" \
    -e SWEEP_MODEL="$model" \
    -e SWEEP_PROFILE="$profile" \
    -e SWEEP_LOGDIR="$SWEEP_LOGDIR" \
    -e SWEEP_OUT_TAG="$SWEEP_OUT_TAG" \
    -e CHERRY_TASKS="$SMOKE_TASK" \
    -v "$ROOT/data:/data" \
    -v "$ROOT/data/container-home-openclaw:/home/node/.openclaw" \
    --memory 8g \
    "$IMAGE" \
    bash /home/node/app/scripts/container_cherry_single.sh > /dev/null 2>&1
  echo "  [$(date '+%H:%M:%S')] [$label] done"
done

echo ""
echo "===== $(date '+%H:%M:%S') SMOKE RESULTS ====="
echo ""
printf "%-12s %-12s %-12s %-12s %s\n" "MODEL" "STATUS" "RUN_SCORE" "C" "FAILURE_MODE"
echo "------------------------------------------------------------------------"
for entry in "${models[@]}"; do
  IFS='|' read -r model profile label <<< "$entry"
  out="$ROOT/data/smoke_10model/docker_${label}_smoke.json"
  if [ ! -f "$out" ]; then
    printf "%-12s %-12s\n" "$label" "NO_OUTPUT"
    continue
  fi
  python3 -c "
import json
try:
    d = json.load(open('$out'))
    t = d['task_results'][0]
    rs = t.get('mean_run_score', 0)
    c = t.get('mean_completion_score', 0)
    fm = t.get('failure_mode_counts', {}) or {}
    fm_str = ','.join(f'{k}={v}' for k,v in fm.items()) if fm else 'ok'
    if rs > 0.3:
        status = 'PASS'
    elif 'timeout' in fm:
        status = 'TIMEOUT'
    elif fm:
        status = 'FAIL'
    else:
        status = 'WEAK'
    print(f'{\"$label\":<12} {status:<12} {rs:<12.3f} {c:<12.3f} {fm_str}')
except Exception as e:
    print(f'{\"$label\":<12} PARSE_ERR    {e}')
"
done

echo ""
echo "===== Gateway-level allowlist / connection errors per model ====="
for entry in "${models[@]}"; do
  IFS='|' read -r model profile label <<< "$entry"
  gwlog="$ROOT/data/smoke_10model/gateway_${label}.log"
  if [ -f "$gwlog" ]; then
    errs=$(grep -cE "model not allowed|sessions.create.*✗|API key|billing|Insufficient" "$gwlog" 2>/dev/null || true)
    errs=${errs:-0}
    if [ "$errs" -gt 0 ]; then
      echo "  [$label] $errs error(s):"
      grep -E "model not allowed|sessions.create.*✗.*errorCode|API key|billing|Insufficient" "$gwlog" 2>/dev/null | head -2 | sed 's/^/    /'
    fi
  fi
done
echo ""
echo "All 10 smoke containers retained for inspection: docker ps -a --filter 'name=clawbench-smoke-'"
"$ROOT/scripts/infra_log_gate.sh" "$ROOT/data/smoke_10model"
