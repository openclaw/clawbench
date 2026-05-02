#!/bin/bash
# Poll the clawbench-sweep container; regenerate drift report every ~5 min
# while it runs, and one final time when it exits. Detached via nohup, so
# it outlives any shell session.

set -u
cd /Users/zhentongfan/Desktop/openclaw/clawbench

REPORT="reports/EVAL_REPORT_7MODEL_DRIFT_2026-04-14-CONTAINER.md"
STATE_DIR="data/drift_2026-04-14"
WATCHLOG="${STATE_DIR}/watcher.log"
mkdir -p "${STATE_DIR}" "$(dirname "$REPORT")"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] watcher start pid=$$ report=${REPORT}" >> "$WATCHLOG"

regen() {
  local tag="$1"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] regen ${tag}" >> "$WATCHLOG"
  python3 scripts/generate_drift_report.py >> "$WATCHLOG" 2>&1 || true
}

last_report_time=0
while true; do
  now=$(date +%s)
  # Container still running?
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^clawbench-sweep$'; then
    # Regenerate live report every 5 min while running
    if (( now - last_report_time >= 300 )); then
      regen "live-snapshot"
      last_report_time=$now
    fi
    sleep 60
  else
    # Container exited — final regen and stop
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] container exited; doing final regen" >> "$WATCHLOG"
    regen "final"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] watcher done" >> "$WATCHLOG"
    touch "${STATE_DIR}/.watcher_done"
    exit 0
  fi
done
