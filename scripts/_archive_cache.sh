#!/bin/bash
# Shared helper sourced by container_sweep_*.sh scripts to snapshot the
# per-model run_cache after a sweep completes. Called at END of each sweep.
#
# Requires these env vars (already set by parent script):
#   CLAWBENCH_RUN_CACHE_DIR   - e.g. /data/run_cache
#   CACHE_SUB                 - e.g. openai_gpt-5.4
#   SWEEP_OUT_TAG             - e.g. v2026-4-18-pr68627-gpt54
#   SWEEP_LABEL               - e.g. gpt54
#   SWEEP_LOGDIR              - e.g. /data/drift_2026-04-18-pr68627-gpt54
#
# Writes snapshot to: /data/run_cache_archive/<SWEEP_OUT_TAG>/<CACHE_SUB>/
# Also writes a metadata.json with sweep label/model/timestamp for indexing.

archive_run_cache() {
  if [ -z "${CACHE_SUB:-}" ]; then
    echo "[archive] skipped: no CACHE_SUB configured"
    return 0
  fi
  local src="${CLAWBENCH_RUN_CACHE_DIR:-/data/run_cache}/$CACHE_SUB"
  if [ ! -d "$src" ]; then
    echo "[archive] skipped: cache dir $src missing"
    return 0
  fi
  local dest_root="/data/run_cache_archive/${SWEEP_OUT_TAG:-untagged}"
  local dest="$dest_root/$CACHE_SUB"
  mkdir -p "$dest_root"
  rm -rf "$dest"  # idempotent — re-running replaces prior snapshot for this tag
  cp -r "$src" "$dest"

  # Write a small metadata.json alongside for quick lookup
  local meta="$dest_root/metadata.json"
  python3 - <<PYEOF
import json, os, datetime
meta_path = "$meta"
# Merge with existing (a single tag may cover multiple models on the same sweep)
existing = {}
if os.path.exists(meta_path):
    try:
        with open(meta_path) as f: existing = json.load(f)
    except Exception:
        existing = {}
entries = existing.setdefault("models", {})
entries["${CACHE_SUB}"] = {
    "sweep_label": "${SWEEP_LABEL:-}",
    "sweep_model": "${SWEEP_MODEL:-}",
    "sweep_out_tag": "${SWEEP_OUT_TAG:-}",
    "sweep_logdir": "${SWEEP_LOGDIR:-}",
    "archived_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "run_count": len([p for p in os.listdir("$src") for r in os.listdir(os.path.join("$src", p)) if r.startswith("run")]) if os.path.isdir("$src") else 0,
}
with open(meta_path, "w") as f: json.dump(existing, f, indent=2)
PYEOF

  local runs
  runs=$(find "$dest" -name "run*.json" 2>/dev/null | wc -l | tr -d ' ')
  echo "[archive] saved $runs transcripts to $dest"
}
