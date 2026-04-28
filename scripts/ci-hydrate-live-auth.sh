#!/usr/bin/env bash
set -euo pipefail

profile_path="${1:-${RUNNER_TEMP:-/tmp}/clawbench-live.profile}"

mkdir -p "$(dirname "$profile_path")"
: >"$profile_path"
chmod 600 "$profile_path"

first_env_value() {
  local key
  for key in "$@"; do
    local value="${!key:-}"
    if [[ -n "$value" && "$value" != "undefined" && "$value" != "null" ]]; then
      printf '%s' "$value"
      return 0
    fi
  done
  return 1
}

append_profile_env() {
  local key="$1"
  local value="${!key:-}"
  if [[ -z "$value" || "$value" == "undefined" || "$value" == "null" ]]; then
    return
  fi
  printf 'export %s=%q\n' "$key" "$value" >>"$profile_path"
}

write_secret_file() {
  local destination="$1"
  shift
  local value=""
  value="$(first_env_value "$@" || true)"
  if [[ -z "$value" ]]; then
    return
  fi
  mkdir -p "$(dirname "$destination")"
  printf '%s' "$value" >"$destination"
  chmod 600 "$destination"
}

for env_key in \
  HF_TOKEN \
  HF_USERNAME \
  CLAWBENCH_QUEUE_DATASET \
  CLAWBENCH_JUDGE_MODEL \
  ANTHROPIC_API_KEY \
  ANTHROPIC_API_KEY_OLD \
  ANTHROPIC_API_TOKEN \
  CEREBRAS_API_KEY \
  DEEPINFRA_API_KEY \
  FIREWORKS_API_KEY \
  GEMINI_API_KEY \
  GOOGLE_API_KEY \
  GROQ_API_KEY \
  KIMI_API_KEY \
  MINIMAX_API_KEY \
  MISTRAL_API_KEY \
  MOONSHOT_API_KEY \
  OPENAI_API_KEY \
  OPENAI_BASE_URL \
  OPENROUTER_API_KEY \
  QWEN_API_KEY \
  TOGETHER_API_KEY \
  XAI_API_KEY \
  ZAI_API_KEY \
  Z_AI_API_KEY
do
  append_profile_env "$env_key"
done

write_secret_file "$HOME/.codex/auth.json" CLAWBENCH_CODEX_AUTH_JSON OPENCLAW_CODEX_AUTH_JSON
write_secret_file "$HOME/.codex/config.toml" CLAWBENCH_CODEX_CONFIG_TOML OPENCLAW_CODEX_CONFIG_TOML
write_secret_file "$HOME/.claude.json" CLAWBENCH_CLAUDE_JSON OPENCLAW_CLAUDE_JSON
write_secret_file "$HOME/.claude/.credentials.json" CLAWBENCH_CLAUDE_CREDENTIALS_JSON OPENCLAW_CLAUDE_CREDENTIALS_JSON
write_secret_file "$HOME/.claude/settings.json" CLAWBENCH_CLAUDE_SETTINGS_JSON OPENCLAW_CLAUDE_SETTINGS_JSON
write_secret_file "$HOME/.claude/settings.local.json" CLAWBENCH_CLAUDE_SETTINGS_LOCAL_JSON OPENCLAW_CLAUDE_SETTINGS_LOCAL_JSON
write_secret_file "$HOME/.gemini/settings.json" CLAWBENCH_GEMINI_SETTINGS_JSON OPENCLAW_GEMINI_SETTINGS_JSON

if [[ -n "${GITHUB_ENV:-}" ]]; then
  {
    echo "CLAWBENCH_PROFILE_FILE=$profile_path"
  } >>"$GITHUB_ENV"
fi
