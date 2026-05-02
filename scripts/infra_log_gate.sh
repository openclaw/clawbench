#!/bin/bash
# Fail if a ClawBench/OpenClaw run directory contains infra-level failures.

set -u

dir="${1:?usage: infra_log_gate.sh <log-dir>}"

if [ ! -d "$dir" ]; then
  echo "[infra-gate] missing log directory: $dir" >&2
  exit 2
fi

pattern="no longer exists|env_unavailable|environment_unavailable|REJECTED|Traceback|model_not_allowed|model not allowed|not allowed|WebSocket closed|API key|billing|Insufficient|sessions.create.*✗|Gateway .*timed out|control-plane.*timed out|connect.*timed out|RPC .*timed out|agents.create timed out|sessions.create.*timed out"

matches="$(rg -n "$pattern" "$dir" 2>/dev/null || true)"
if [ -n "$matches" ]; then
  echo "[infra-gate] infra-level signatures found in $dir" >&2
  printf '%s\n' "$matches" | head -80 >&2
  exit 1
fi

echo "[infra-gate] clean: $dir"
