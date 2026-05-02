#!/bin/bash
# Third-attempt validation of the OpenRouter reasoning-only stop-turn fix:
# branch codex/openrouter-completions-general, HEAD ceacf62d63
# "fix(openrouter): treat completions reasoning as unsigned"
#
# This layers on top of v2's stream-output rewrite. v3 stops attaching the
# placeholder `thinkingSignature: "reasoning_details"` to unsigned provider
# reasoning blocks, so downstream code (pi-embedded-runner/thinking.ts,
# transport-message-transform.ts) no longer treats GLM/MiniMax/Kimi
# reasoning as signed thinking that needs validation.
#
# Output: data/drift_2026-04-16-userfix-v3/docker_{glm,minimax,kimi}_v2026-4-16-userfix-v3.json

set -eu

IMG="${IMG:-clawbench-clawbench:user-fix}"
ROOT="/Users/zhentongfan/Desktop/openclaw/clawbench"
LOGDIR="/data/drift_2026-04-16-userfix-v3"
OUT_TAG="v2026-4-16-userfix-v3"

mkdir -p "$ROOT/data/drift_2026-04-16-userfix-v3"

declare -a runs=(
  "glm|openrouter/z-ai/glm-5.1|/home/node/app/profiles/frontier_glm_5_1.yaml"
  "minimax|openrouter/minimax/minimax-m2.7|/home/node/app/profiles/frontier_minimax_m27.yaml"
  "kimi|openrouter/moonshotai/kimi-k2.5|/home/node/app/profiles/frontier_kimi_k25.yaml"
)

for entry in "${runs[@]}"; do
  IFS='|' read -r label model profile <<< "$entry"
  name="clawbench-userfix-v3-${label}"
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
echo "  docker logs -f clawbench-userfix-v3-glm"
echo "  docker logs -f clawbench-userfix-v3-minimax"
echo "  docker logs -f clawbench-userfix-v3-kimi"
