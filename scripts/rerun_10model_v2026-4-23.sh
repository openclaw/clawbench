#!/bin/bash
# 10-model sweep against OpenClaw 2026.4.26 (latest beta).
# Provider-balanced 2-wave parallel execution (5 containers per wave).
#
# Wave 1: Anthropic + OpenAI + Google + DeepSeek + OpenRouter (5 different providers)
# Wave 2: Anthropic + OpenAI + Anthropic + OpenRouter + OpenRouter
#
# Each container:
#   - state-isolated /tmp/openclaw-state-<label>-<pid>
#   - bumped Node heap (--max-old-space-size=4096)
#   - clears CACHE_SUB before run, archives at end
#
# Output:
#   data/drift_2026-04-23/docker_<label>_v2026-4-23.{json,log}
#   data/run_cache_archive/v2026-4-23/<cache_sub>/<task>/runN.json

set -u

ROOT="/Users/zhentongfan/Desktop/openclaw/clawbench"
cd "$ROOT"

IMAGE="clawbench-clawbench:latest"   # OpenClaw 2026.4.26
SWEEP_LOGDIR="/data/drift_2026-04-23"
SWEEP_OUT_TAG="v2026-4-23"

# wave | model | profile (container path) | label
declare -a wave1=(
  "anthropic/claude-opus-4-6|/home/node/app/profiles/frontier_opus_4_6.yaml|opus46"
  "openai/gpt-5.4|/home/node/app/profiles/frontier_gpt_5_4.yaml|gpt54"
  "google/gemini-3.1-pro-preview|/home/node/app/profiles/frontier_gemini_3_pro.yaml|gemini"
  "deepseek/v4-pro|/home/node/app/profiles/frontier_deepseek_v4.yaml|deepseek"
  "openrouter/moonshotai/kimi-k2.6|/home/node/app/profiles/frontier_kimi_k26.yaml|kimi26"
)
declare -a wave2=(
  "anthropic/claude-opus-4-7|/home/node/app/profiles/frontier_opus_4_7.yaml|opus47"
  "openai/gpt-5.5|/home/node/app/profiles/frontier_gpt_5_5.yaml|gpt55"
  "anthropic/claude-sonnet-4-6|/home/node/app/profiles/frontier_sonnet_4_6.yaml|sonnet46"
  "openrouter/minimax/minimax-m2.7|/home/node/app/profiles/frontier_minimax_m27.yaml|minimax"
  "openrouter/z-ai/glm-5.1|/home/node/app/profiles/frontier_glm_5_1.yaml|glm"
)

mkdir -p "$ROOT/data/drift_2026-04-23"

launch_wave() {
  local wave_name="$1"
  shift
  local entries=("$@")
  echo ""
  echo "========================================================================"
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') $wave_name — launching ${#entries[@]} containers in parallel ====="
  echo "========================================================================"

  local names=()
  for entry in "${entries[@]}"; do
    IFS='|' read -r model profile label <<< "$entry"
    name="clawbench-10m-${label}"
    names+=("$name")
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run -d --name "$name" \
      -e SWEEP_LABEL="$label" \
      -e SWEEP_MODEL="$model" \
      -e SWEEP_PROFILE="$profile" \
      -e SWEEP_LOGDIR="$SWEEP_LOGDIR" \
      -e SWEEP_OUT_TAG="$SWEEP_OUT_TAG" \
      -v "$ROOT/scripts:/home/node/app/scripts:ro" \
      -v "$ROOT/data:/data" \
      -v "$ROOT/data/container-home-openclaw:/home/node/.openclaw" \
      -v "$ROOT/profiles:/home/node/app/profiles:ro" \
      --memory 8g \
      "$IMAGE" \
      bash /home/node/app/scripts/container_sweep_single.sh
    echo "  [$label] launched ($model)"
  done

  echo ""
  echo "===== $(date '+%H:%M:%S') waiting for $wave_name to finish (${#entries[@]} containers) ====="
  for name in "${names[@]}"; do
    docker wait "$name"
    status=$?
    label=${name#clawbench-10m-}
    if [ $status -eq 0 ]; then
      echo "  [$label] exit 0"
    else
      echo "  [$label] FAILED exit=$status"
      docker logs --tail 5 "$name" 2>&1 | sed "s/^/    [$label] /"
    fi
  done
  echo "===== $(date '+%H:%M:%S') $wave_name complete ====="
}

# Allow filtering: pass a wave name (wave1 or wave2) to run only that
case "${1:-all}" in
  wave1) launch_wave "WAVE 1" "${wave1[@]}" ;;
  wave2) launch_wave "WAVE 2" "${wave2[@]}" ;;
  all)
    launch_wave "WAVE 1" "${wave1[@]}"
    launch_wave "WAVE 2" "${wave2[@]}"
    ;;
  *) echo "usage: $0 [wave1|wave2|all]"; exit 1 ;;
esac

echo ""
echo "===== ALL DONE $(date '+%Y-%m-%d %H:%M:%S') ====="
echo "Results: $ROOT/data/drift_2026-04-23/"
echo "Archives: $ROOT/data/run_cache_archive/$SWEEP_OUT_TAG/"
