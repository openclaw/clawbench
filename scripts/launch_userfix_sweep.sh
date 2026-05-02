#!/bin/bash
# Launch 3 parallel clawbench containers (glm, minimax, kimi) against the
# user-fix openclaw image, mirroring the v4.15-beta.1 sweep pattern.
#
# Output goes to data/drift_2026-04-16-userfix/ with tag v2026-4-16-userfix,
# so generate_drift_report.py can be updated to pick it up alongside the
# v4.14 and v4.15-beta.1 results.

set -eu

IMG="${IMG:-clawbench-clawbench:user-fix}"
ROOT="/Users/zhentongfan/Desktop/openclaw/clawbench"
LOGDIR="/data/drift_2026-04-16-userfix"
OUT_TAG="v2026-4-16-userfix"

mkdir -p "$ROOT/data/drift_2026-04-16-userfix"

declare -a runs=(
  "glm|openrouter/z-ai/glm-5.1|/home/node/app/profiles/frontier_glm_5_1.yaml"
  "minimax|openrouter/minimax/minimax-m2.7|/home/node/app/profiles/frontier_minimax_m27.yaml"
  "kimi|openrouter/moonshotai/kimi-k2.5|/home/node/app/profiles/frontier_kimi_k25.yaml"
)

for entry in "${runs[@]}"; do
  IFS='|' read -r label model profile <<< "$entry"
  name="clawbench-userfix-${label}"
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
echo "  docker logs -f clawbench-userfix-glm"
echo "  docker logs -f clawbench-userfix-minimax"
echo "  docker logs -f clawbench-userfix-kimi"
