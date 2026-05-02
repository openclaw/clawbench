#!/usr/bin/env bash
# Prepare a lane-local GBrain install for OpenClaw benchmark runs.
#
# The image supplies /opt/gbrain and this script keeps secrets runtime-only:
# keys are read from the lane's openclaw.json env block or existing process env,
# never baked into Docker layers.
set -Eeuo pipefail

if [ "${CLAWBENCH_ENABLE_GBRAIN:-0}" != "1" ]; then
  exit 0
fi

: "${HOME:?HOME is required}"

GBRAIN_ROOT="${GBRAIN_ROOT:-/opt/gbrain}"
if [ ! -d "$GBRAIN_ROOT" ]; then
  echo "[gbrain] missing $GBRAIN_ROOT" >&2
  exit 1
fi

export PATH="$GBRAIN_ROOT/bin:/usr/local/bun/bin:$PATH"
export GBRAIN_ALLOW_SHELL_JOBS="${GBRAIN_ALLOW_SHELL_JOBS:-1}"

STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"
CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-$STATE_DIR/openclaw.json}"
LOG_DIR="${CLAWBENCH_GBRAIN_LOG_DIR:-$STATE_DIR/logs}"
mkdir -p "$HOME/.gbrain" "$LOG_DIR"
LOG_PATH="$LOG_DIR/gbrain-runtime.log"

if [ -f "$CONFIG_PATH" ]; then
  eval "$(python3 - "$CONFIG_PATH" <<'PY'
import json
import os
import shlex
import sys

config_path = sys.argv[1]
try:
    data = json.load(open(config_path, encoding="utf-8"))
except Exception:
    data = {}
env = data.get("env") if isinstance(data, dict) else {}
if not isinstance(env, dict):
    env = {}
for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
    value = os.environ.get(key) or env.get(key)
    if value:
        print(f"export {key}={shlex.quote(str(value))}")
PY
)"

  python3 - "$CONFIG_PATH" "$GBRAIN_ROOT" <<'PY'
import json
import sys

config_path = sys.argv[1]
gbrain_root = sys.argv[2]
try:
    with open(config_path, encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    data = {}
if not isinstance(data, dict):
    data = {}

plugins = data.setdefault("plugins", {})
if not isinstance(plugins, dict):
    plugins = {}
    data["plugins"] = plugins

allow = plugins.get("allow")
if not isinstance(allow, list):
    allow = []
plugins["allow"] = allow
if "gbrain" not in allow:
    allow.append("gbrain")

entries = plugins.get("entries")
if not isinstance(entries, dict):
    entries = {}
plugins["entries"] = entries
entry = entries.get("gbrain")
if not isinstance(entry, dict):
    entry = {}
entries["gbrain"] = entry
entry["enabled"] = True

load = plugins.get("load")
if not isinstance(load, dict):
    load = {}
plugins["load"] = load
paths = load.get("paths")
if not isinstance(paths, list):
    paths = []
load["paths"] = paths
if gbrain_root not in paths:
    paths.append(gbrain_root)

with open(config_path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2)
    handle.write("\n")
PY
fi

echo "[gbrain] preparing HOME=$HOME" > "$LOG_PATH"
echo "[gbrain] version: $(gbrain --version 2>/dev/null || true)" >> "$LOG_PATH"
echo "[gbrain] plugin path enabled in $CONFIG_PATH" >> "$LOG_PATH"

if [ ! -f "$HOME/.gbrain/config.json" ]; then
  gbrain init >> "$LOG_PATH" 2>&1
else
  gbrain apply-migrations --yes --non-interactive >> "$LOG_PATH" 2>&1 || true
fi

BRAIN_REPO="${GBRAIN_BRAIN_REPO:-$HOME/brain}"
mkdir -p "$BRAIN_REPO"
if [ "${CLAWBENCH_GBRAIN_SEED_SMOKE:-1}" = "1" ] && ! find "$BRAIN_REPO" -type f -name '*.md' -print -quit | grep -q .; then
  cat > "$BRAIN_REPO/gbrain-smoke.md" <<'EOF'
# GBrain smoke page

This page verifies that the benchmark image can initialize, import, and query a
lane-local GBrain database. It is intentionally generic and not task-specific.
EOF
fi

if find "$BRAIN_REPO" -type f -name '*.md' -print -quit | grep -q .; then
  gbrain import "$BRAIN_REPO" --no-embed >> "$LOG_PATH" 2>&1 || true
  if [ -n "${OPENAI_API_KEY:-}" ]; then
    gbrain embed --stale >> "$LOG_PATH" 2>&1 || true
  else
    echo "[gbrain] OPENAI_API_KEY not available; semantic embeddings skipped" >> "$LOG_PATH"
  fi
fi

gbrain doctor --json >> "$LOG_PATH" 2>&1 || true
echo "[gbrain] ready" >> "$LOG_PATH"
