#!/bin/bash
# Run the repaired v4.9 40-task set on latest OpenClaw with lane-level
# parallelism, while limiting model-container concurrency to avoid host OOM.

set -Eeuo pipefail

ROOT="${ROOT:-/Users/zhentongfan/Desktop/openclaw/clawbench}"
IMAGE="${IMAGE:-clawbench-clawbench:v2026-4-26-agent-hotfix}"
LOGDIR_CONT="${SWEEP_LOGDIR:-/data/drift_2026-04-28-v49-openclaw-426-hotfix}"
OUT_TAG="${SWEEP_OUT_TAG:-v49-openclaw-426-hotfix}"
RUNS="${SWEEP_RUNS:-3}"
LANES="${SWEEP_LANES:-1}"
BATCH_SIZE="${BATCH_SIZE:-2}"
MEMORY="${DOCKER_MEMORY:-8g}"
NAME_PREFIX="${NAME_PREFIX:-clawbench-v49-batched}"

TASKS="t3-debug-timezone-regression,t3-social-bill-split,t1-cal-quick-reminder,t4-cross-repo-migration,t5-contradictory-requirements,t3-node-multifile-refactor,t2-add-tests-normalizer,t3-cal-reschedule-cascade,t1-life-translate,t5-impossible-graceful-fail,t2-skill-excel-rollup,t1-fs-quick-note,t2-priv-redact-doc,t1-architecture-brief,t4-memory-recall-continuation,t2-fs-cleanup-downloads,t3-web-research-and-cite,t2-config-loader,t2-sys-memory-roundtrip,t4-ctx-long-recall,t3-fin-budget-monthly,t2-fs-find-that-thing,t3-data-pipeline-report,t2-ctx-pronoun-resolve,t5-hallucination-resistant-evidence,t2-msg-summarize-thread,t2-log-analyzer-cli,t2-node-search-patch,t4-delegation-repair,t3-data-sql-query,t2-web-quick-fact,t1-bugfix-discount,t4-life-trip-plan,t3-feature-export,t1-refactor-csv-loader,t3-monitoring-automation,t3-msg-inbox-triage,t2-browser-form-fix,t4-browser-research-and-code,t2-err-instruction-ambig"

declare -a runs=(
  "gpt55|openai/gpt-5.5"
  "gpt54|openai/gpt-5.4"
  "deepseekv4|openrouter/deepseek/deepseek-v4-pro"
  "opus47|anthropic/claude-opus-4-7"
  "opus46|anthropic/claude-opus-4-6"
  "sonnet46|anthropic/claude-sonnet-4-6"
  "minimax27|openrouter/minimax/minimax-m2.7"
  "kimi26|openrouter/moonshotai/kimi-k2.6"
  "glm51|openrouter/z-ai/glm-5.1"
  "gemini31pro|google/gemini-3.1-pro-preview"
)

cd "$ROOT"
LOGDIR_HOST="$ROOT/${LOGDIR_CONT#/data/}"
if [[ "$LOGDIR_CONT" == /data/* ]]; then
  LOGDIR_HOST="$ROOT/data/${LOGDIR_CONT#/data/}"
fi
mkdir -p "$LOGDIR_HOST"

echo "===== V49 LATEST BATCHED RUN $(date '+%Y-%m-%d %H:%M:%S') ====="
echo "image:      $IMAGE"
echo "logdir:     $LOGDIR_HOST"
echo "out_tag:    $OUT_TAG"
echo "runs:       $RUNS"
echo "lanes:      $LANES"
echo "batch_size: $BATCH_SIZE"
echo "memory:     $MEMORY"
echo

failures=0
for ((i = 0; i < ${#runs[@]}; i += BATCH_SIZE)); do
  batch=("${runs[@]:i:BATCH_SIZE}")
  names=()
  echo "===== launching batch $((i / BATCH_SIZE + 1)) at $(date '+%H:%M:%S') ====="
  for entry in "${batch[@]}"; do
    IFS='|' read -r label model <<< "$entry"
    name="$NAME_PREFIX-$label"
    names+=("$name")
    docker rm -f "$name" >/dev/null 2>&1 || true
    cid=$(docker run -d --name "$name" \
      -e SWEEP_LABEL="$label" \
      -e SWEEP_MODEL="$model" \
      -e SWEEP_LOGDIR="$LOGDIR_CONT" \
      -e SWEEP_OUT_TAG="$OUT_TAG" \
      -e SWEEP_RUNS="$RUNS" \
      -e SWEEP_LANES="$LANES" \
      -e SWEEP_TASKS="$TASKS" \
      -e OPENCLAW_CONFIG_SOURCE=/config/openclaw \
      -e OPENCLAW_EXEC_HOST=gateway \
      -e CLAWBENCH_PER_RUN_BUDGET_SECONDS="${CLAWBENCH_PER_RUN_BUDGET_SECONDS:-900}" \
      -e CLAWBENCH_PER_TURN_TIMEOUT_SECONDS="${CLAWBENCH_PER_TURN_TIMEOUT_SECONDS:-300}" \
      -e CLAWBENCH_GATEWAY_PROBE_TIMEOUT_SECONDS="${CLAWBENCH_GATEWAY_PROBE_TIMEOUT_SECONDS:-180}" \
      -v "$ROOT/data:/data" \
      -v "$ROOT/data/container-home-openclaw:/config/openclaw:ro" \
      --memory "$MEMORY" \
      "$IMAGE" \
      bash /home/node/app/scripts/container_lane_eval.sh)
    printf '%-24s %-44s %s\n' "$name" "$model" "$cid"
  done

  echo "===== waiting batch $((i / BATCH_SIZE + 1)) ====="
  for name in "${names[@]}"; do
    set +e
    status="$(docker wait "$name")"
    wait_status=$?
    set -e
    if [[ "$wait_status" != "0" ]]; then
      status="docker-wait-failed-$wait_status"
    fi
    echo "===== $name exit=$status $(date '+%H:%M:%S') ====="
    docker logs --tail 80 "$name" 2>&1 || true
    if [[ "$status" != "0" ]]; then
      failures=$((failures + 1))
    fi
  done
  echo
done

echo "===== BATCHED RUN DONE $(date '+%Y-%m-%d %H:%M:%S') failures=$failures ====="
echo "Results: $LOGDIR_HOST"
"$ROOT/scripts/infra_log_gate.sh" "$LOGDIR_HOST" || failures=$((failures + 1))
exit "$failures"
