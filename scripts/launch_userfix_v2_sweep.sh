#!/bin/bash
# Second-attempt validation of the OpenRouter reasoning-only stop-turn fix:
# branch codex/openrouter-completions-general, HEAD 872cfefe58
# "fix(openrouter): preserve reasoning-only stop turns"
#
# This layers a stream-output rewrite: when the OpenRouter SSE ends with
# finish_reason=stop and only reasoning/thinking blocks (no visible text),
# synthesize a text block from the reasoning so downstream run-liveness
# doesn't abandon the turn.
#
# Output goes to data/drift_2026-04-16-userfix-v2/ with tag
# v2026-4-16-userfix-v2, so generate_drift_report.py can pick it up
# alongside the prior user-fix, v4.14, and v4.15-beta.1 sweeps.

set -eu

IMG="${IMG:-clawbench-clawbench:user-fix}"
ROOT="/Users/zhentongfan/Desktop/openclaw/clawbench"
LOGDIR="/data/drift_2026-04-16-userfix-v2"
OUT_TAG="v2026-4-16-userfix-v2"

mkdir -p "$ROOT/data/drift_2026-04-16-userfix-v2"

declare -a runs=(
  "glm|openrouter/z-ai/glm-5.1|/home/node/app/profiles/frontier_glm_5_1.yaml"
  "minimax|openrouter/minimax/minimax-m2.7|/home/node/app/profiles/frontier_minimax_m27.yaml"
  "kimi|openrouter/moonshotai/kimi-k2.5|/home/node/app/profiles/frontier_kimi_k25.yaml"
)

for entry in "${runs[@]}"; do
  IFS='|' read -r label model profile <<< "$entry"
  name="clawbench-userfix-v2-${label}"
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
echo "  docker logs -f clawbench-userfix-v2-glm"
echo "  docker logs -f clawbench-userfix-v2-minimax"
echo "  docker logs -f clawbench-userfix-v2-kimi"
