#!/bin/bash
# Full 9-model solo re-sweep with all v4.19 fixes applied:
#   - State-dir isolation per sweep (container_sweep_single.sh OPENCLAW_STATE_DIR)
#   - 4 verifiers fixed (t2-ctx-pronoun-resolve, t2-sys-memory-roundtrip,
#                       t3-fin-budget-monthly, t4-ctx-long-recall)
#   - kimi-k2.6 removed (unsupported in current openclaw version)
#
# Each model runs in its own dedicated Docker container with pristine
# OPENCLAW_STATE_DIR, sequentially to avoid cross-model gateway contention.
#
# Usage:
#   bash scripts/rerun_9model_v2026-4-20.sh               # run all 9 models
#   bash scripts/rerun_9model_v2026-4-20.sh glm minimax   # run specific subset
#
# Logs/results land in data/drift_2026-04-20-full/ and cache archives under
# data/run_cache_archive/v2026-4-20-full/.

set -u

ROOT="/Users/zhentongfan/Desktop/openclaw/clawbench"
cd "$ROOT"

SWEEP_LOGDIR="/data/drift_2026-04-20-full"
SWEEP_OUT_TAG="v2026-4-20-full"

# model | profile (container path) | label
declare -a runs=(
  "anthropic/claude-opus-4-7|/home/node/app/profiles/frontier_opus_4_7.yaml|opus47"
  "anthropic/claude-opus-4-6|/home/node/app/profiles/frontier_opus_4_6.yaml|opus46"
  "anthropic/claude-sonnet-4-6|/home/node/app/profiles/frontier_sonnet_4_6.yaml|sonnet46"
  "openai/gpt-5.4|/home/node/app/profiles/frontier_gpt_5_4.yaml|gpt54"
  "google/gemini-3.1-pro-preview|/home/node/app/profiles/frontier_gemini_3_pro.yaml|gemini"
  "openrouter/z-ai/glm-5.1|/home/node/app/profiles/frontier_glm_5_1.yaml|glm"
  "openrouter/minimax/minimax-m2.7|/home/node/app/profiles/frontier_minimax_m27.yaml|minimax"
  "openrouter/moonshotai/kimi-k2.5|/home/node/app/profiles/frontier_kimi_k25.yaml|kimi25"
  "openrouter/qwen/qwen3.6-plus|/home/node/app/profiles/frontier_qwen_3_6.yaml|qwen"
)

# Optional filter: pass labels as positional args to run only those models
FILTER=("$@")
should_run() {
  local label="$1"
  [ ${#FILTER[@]} -eq 0 ] && return 0
  for f in "${FILTER[@]}"; do
    [ "$f" = "$label" ] && return 0
  done
  return 1
}

mkdir -p "$ROOT/data/drift_2026-04-20-full"

for entry in "${runs[@]}"; do
  IFS='|' read -r model profile label <<< "$entry"
  if ! should_run "$label"; then
    echo "[skip] $label (not in filter)"
    continue
  fi

  name="clawbench-9m-${label}"
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') starting $label ($model) ====="

  # Kill any previous container with the same name
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
    clawbench-clawbench:latest \
    bash /home/node/app/scripts/container_sweep_single.sh

  # Wait for this model to finish before starting the next (sequential)
  echo "[$label] container launched; waiting for completion..."
  docker wait "$name"
  status=$?
  echo "===== $(date '+%H:%M:%S') $label container exit=$status ====="

  # Keep the container around briefly in case we need to inspect logs
  docker logs --tail 20 "$name" | sed "s/^/[$label] /"
done

echo "===== ALL DONE $(date '+%Y-%m-%d %H:%M:%S') ====="
echo "Results: $ROOT/data/drift_2026-04-20-full/"
echo "Archives: $ROOT/data/run_cache_archive/$SWEEP_OUT_TAG/"
