#!/bin/bash
# Cherry-pick re-run: only the task/runs affected by our infra/task fixes.
#
# Scope per model:
#   - 4 fixed tasks (t2-ctx-pronoun-resolve, t2-sys-memory-roundtrip,
#                   t3-fin-budget-monthly, t4-ctx-long-recall) — prompt+verifier
#                   changed, so results from v4-19-full aren't valid.
#   - Any task that was missing from the v4-19-full archive (infra crash).
#
# Each model runs in its own container with state-isolation (patched
# container_sweep_single.sh), using the rebuilt 2026-04-20 Docker image
# which has the new task prompts and verifiers baked in.
#
# Writes results to data/run_cache_archive/v2026-4-20-cherry/<cache_sub>/.
# Merge with v2026-4-19-full archive happens via merge_cherrypick.py.
#
# Usage:
#   bash scripts/cherrypick_rerun.sh              # all 9 models
#   bash scripts/cherrypick_rerun.sh opus47 glm   # specific subset

set -u

ROOT="/Users/zhentongfan/Desktop/openclaw/clawbench"
cd "$ROOT"

SWEEP_LOGDIR="/data/drift_2026-04-20-cherry"
SWEEP_OUT_TAG="v2026-4-20-cherry"

# Fixed tasks — always re-run these across all models
FIXED_TASKS="t2-ctx-pronoun-resolve,t2-sys-memory-roundtrip,t3-fin-budget-monthly,t4-ctx-long-recall"

# model | profile | label | extra_tasks (comma-separated; empty means just fixed tasks)
declare -a runs=(
  "anthropic/claude-opus-4-7|/home/node/app/profiles/frontier_opus_4_7.yaml|opus47|t5-contradictory-requirements"
  "anthropic/claude-opus-4-6|/home/node/app/profiles/frontier_opus_4_6.yaml|opus46|t1-cal-quick-reminder"
  "anthropic/claude-sonnet-4-6|/home/node/app/profiles/frontier_sonnet_4_6.yaml|sonnet46|t4-memory-recall-continuation"
  "openai/gpt-5.4|/home/node/app/profiles/frontier_gpt_5_4.yaml|gpt54|t2-browser-form-fix,t3-debug-timezone-regression,t4-browser-research-and-code"
  "google/gemini-3.1-pro-preview|/home/node/app/profiles/frontier_gemini_3_pro.yaml|gemini|"
  "openrouter/z-ai/glm-5.1|/home/node/app/profiles/frontier_glm_5_1.yaml|glm|t1-architecture-brief,t1-fs-quick-note,t2-config-loader,t2-msg-summarize-thread,t3-debug-timezone-regression,t3-monitoring-automation,t3-msg-inbox-triage,t3-social-bill-split"
  "openrouter/minimax/minimax-m2.7|/home/node/app/profiles/frontier_minimax_m27.yaml|minimax|t1-bugfix-discount,t5-contradictory-requirements"
  "openrouter/moonshotai/kimi-k2.5|/home/node/app/profiles/frontier_kimi_k25.yaml|kimi25|t4-life-trip-plan"
  "openrouter/qwen/qwen3.6-plus|/home/node/app/profiles/frontier_qwen_3_6.yaml|qwen|"
)

FILTER=("$@")
should_run() {
  local label="$1"
  [ ${#FILTER[@]} -eq 0 ] && return 0
  for f in "${FILTER[@]}"; do [ "$f" = "$label" ] && return 0; done
  return 1
}

mkdir -p "$ROOT/data/drift_2026-04-20-cherry"

for entry in "${runs[@]}"; do
  IFS='|' read -r model profile label extra <<< "$entry"
  if ! should_run "$label"; then
    echo "[skip] $label (not in filter)"
    continue
  fi

  # Build task list: fixed + extra
  if [ -n "$extra" ]; then
    tasks="$FIXED_TASKS,$extra"
  else
    tasks="$FIXED_TASKS"
  fi
  # Count tasks for logging
  ntasks=$(echo "$tasks" | tr ',' '\n' | wc -l | tr -d ' ')

  name="clawbench-cherry-${label}"
  echo "===== $(date '+%H:%M:%S') starting $label ($model) — $ntasks tasks ====="
  docker rm -f "$name" >/dev/null 2>&1 || true

  # Run via a custom cherry-pick script (embedded below) that accepts TASKS env var
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
    clawbench-clawbench:latest \
    bash /home/node/app/scripts/container_cherry_single.sh

  echo "[$label] waiting for completion..."
  docker wait "$name"
  status=$?
  echo "===== $(date '+%H:%M:%S') $label done (exit=$status) ====="
  docker logs --tail 15 "$name" | sed "s/^/[$label] /"
done

echo "===== CHERRY-PICK ALL DONE $(date '+%H:%M:%S') ====="
echo "Archives: $ROOT/data/run_cache_archive/$SWEEP_OUT_TAG/"
echo ""
echo "Next: run scripts/merge_cherrypick.py to produce v2026-4-20-final"
