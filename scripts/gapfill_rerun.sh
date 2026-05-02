#!/bin/bash
# Fill in crashed/missing runs from v4-19-full sweep.
# Uses identical conditions (original tasks, original verifiers, same Docker image
# rebuilt with original code) so the new runs are statistically interchangeable
# with the existing v4-19-full data.
#
# Per-model missing runs (from audit):
#   opus47:   1  t5-contradictory-requirements:0
#   opus46:   1  t1-cal-quick-reminder:2
#   sonnet46: 4  t2-ctx-pronoun-resolve:0,1; t4-memory-recall-continuation:1,2
#   gpt54:    3  t2-browser-form-fix:1; t3-debug-timezone-regression:0; t4-browser-research-and-code:0
#   gemini:   2  t2-ctx-pronoun-resolve:0,1
#   glm:      3  t2-sys-memory-roundtrip:2; t2-web-quick-fact:2; t4-memory-recall-continuation:0
#   minimax:  2  t1-bugfix-discount:2; t5-contradictory-requirements:2
#   kimi25:   1  t4-life-trip-plan:1
#   qwen:     3  t2-ctx-pronoun-resolve:0,1,2
#
# Each container runs 3 fresh attempts of each needed task. Post-run, only the
# specific missing-run indices are spliced into v4-19-full archive.

set -u

ROOT="/Users/zhentongfan/Desktop/openclaw/clawbench"
cd "$ROOT"

# Use user-fix-opus47 image — same OpenClaw 2026.4.15-beta.1 that the original
# v4-19-full sweep ran on (matches the 1040 non-gap-fill runs' platform version).
IMAGE="clawbench-clawbench:user-fix-opus47"
SWEEP_LOGDIR="/data/drift_2026-04-20-gapfill-v2"
SWEEP_OUT_TAG="v2026-4-20-gapfill-v2"

# model | profile (container path) | label | tasks (comma sep)
declare -a runs=(
  "anthropic/claude-opus-4-7|/home/node/app/profiles/frontier_opus_4_7.yaml|opus47|t5-contradictory-requirements"
  "anthropic/claude-opus-4-6|/home/node/app/profiles/frontier_opus_4_6.yaml|opus46|t1-cal-quick-reminder"
  "anthropic/claude-sonnet-4-6|/home/node/app/profiles/frontier_sonnet_4_6.yaml|sonnet46|t2-ctx-pronoun-resolve,t4-memory-recall-continuation"
  "openai/gpt-5.4|/home/node/app/profiles/frontier_gpt_5_4.yaml|gpt54|t2-browser-form-fix,t3-debug-timezone-regression,t4-browser-research-and-code"
  "google/gemini-3.1-pro-preview|/home/node/app/profiles/frontier_gemini_3_pro.yaml|gemini|t2-ctx-pronoun-resolve"
  "openrouter/z-ai/glm-5.1|/home/node/app/profiles/frontier_glm_5_1.yaml|glm|t2-sys-memory-roundtrip,t2-web-quick-fact,t4-memory-recall-continuation"
  "openrouter/minimax/minimax-m2.7|/home/node/app/profiles/frontier_minimax_m27.yaml|minimax|t1-bugfix-discount,t5-contradictory-requirements"
  "openrouter/moonshotai/kimi-k2.5|/home/node/app/profiles/frontier_kimi_k25.yaml|kimi25|t4-life-trip-plan"
  "openrouter/qwen/qwen3.6-plus|/home/node/app/profiles/frontier_qwen_3_6.yaml|qwen|t2-ctx-pronoun-resolve"
)

FILTER=("$@")
should_run() {
  local l="$1"
  [ ${#FILTER[@]} -eq 0 ] && return 0
  for f in "${FILTER[@]}"; do [ "$f" = "$l" ] && return 0; done
  return 1
}

mkdir -p "$ROOT/data/drift_2026-04-20-gapfill"

for entry in "${runs[@]}"; do
  IFS='|' read -r model profile label tasks <<< "$entry"
  if ! should_run "$label"; then
    echo "[skip] $label"
    continue
  fi

  name="clawbench-gap-${label}"
  echo "===== $(date '+%H:%M:%S') starting $label ($tasks) ====="
  docker rm -f "$name" >/dev/null 2>&1 || true

  docker run -d --name "$name" \
    -e SWEEP_LABEL="$label" \
    -e SWEEP_MODEL="$model" \
    -e SWEEP_PROFILE="$profile" \
    -e SWEEP_LOGDIR="$SWEEP_LOGDIR" \
    -e SWEEP_OUT_TAG="$SWEEP_OUT_TAG" \
    -e CHERRY_TASKS="$tasks" \
    -v "$ROOT/scripts:/home/node/app/scripts:ro" \
    -v "$ROOT/data:/data" \
    -v "$ROOT/data/container-home-openclaw:/home/node/.openclaw" \
    -v "$ROOT/profiles:/home/node/app/profiles:ro" \
    --memory 8g \
    "$IMAGE" \
    bash /home/node/app/scripts/container_cherry_single.sh

  echo "[$label] waiting..."
  docker wait "$name"
  status=$?
  echo "===== $(date '+%H:%M:%S') $label exit=$status ====="
  docker logs --tail 8 "$name" 2>&1 | sed "s/^/[$label] /"
done

echo "===== GAPFILL DONE $(date '+%H:%M:%S') ====="
echo "Next: python3 scripts/splice_gapfill.py"
