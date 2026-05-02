#!/bin/bash
# Fourth-attempt validation — branch codex/openrouter-completions-general,
# HEAD 3fcb9124e1
# "refactor(runner): name visible-answer recovery policy"
# + 63167616a3 "fix(runner): scope gpt empty-turn recovery"
#
# v4 narrows incomplete-turn retry trigger to OpenAI/GPT only.
# For OpenRouter lanes, reasoning-only turns are marked
# livenessState: "working" instead of triggering retry loops —
# fixing the 180s turn-hang regression we saw in v3.
#
# Output: data/drift_2026-04-16-userfix-v4/docker_{glm,minimax,kimi}_v2026-4-16-userfix-v4.json

set -eu

IMG="${IMG:-clawbench-clawbench:user-fix}"
ROOT="/Users/zhentongfan/Desktop/openclaw/clawbench"
LOGDIR="/data/drift_2026-04-16-userfix-v4"
OUT_TAG="v2026-4-16-userfix-v4"

mkdir -p "$ROOT/data/drift_2026-04-16-userfix-v4"

declare -a runs=(
  "glm|openrouter/z-ai/glm-5.1|/home/node/app/profiles/frontier_glm_5_1.yaml"
  "minimax|openrouter/minimax/minimax-m2.7|/home/node/app/profiles/frontier_minimax_m27.yaml"
  "kimi|openrouter/moonshotai/kimi-k2.5|/home/node/app/profiles/frontier_kimi_k25.yaml"
)

for entry in "${runs[@]}"; do
  IFS='|' read -r label model profile <<< "$entry"
  name="clawbench-userfix-v4-${label}"
  echo "starting $name ($model) ..."
  docker run -d --name "$name" \
    -e SWEEP_LABEL="$label" \
    -e SWEEP_MODEL="$model" \
    -e SWEEP_PROFILE="$profile" \
    -e SWEEP_LOGDIR="$LOGDIR" \
    -e SWEEP_OUT_TAG="$OUT_TAG" \
    -v "$ROOT/scripts:/home/node/app/scripts:ro" \
    -v "$ROOT/data:/data" \
    -v "$ROOT/data/container-home-openclaw:/home/node/.openclaw" \
    -v "$ROOT/profiles:/home/node/app/profiles:ro" \
    --memory 8g \
    "$IMG" \
    bash /home/node/app/scripts/container_sweep_single.sh
done

echo ""
echo "all 3 containers started. tail logs with:"
echo "  docker logs -f clawbench-userfix-v4-glm"
echo "  docker logs -f clawbench-userfix-v4-minimax"
echo "  docker logs -f clawbench-userfix-v4-kimi"
