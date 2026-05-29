#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

PROVIDER="${MEETING_LLM_PROVIDER:-claude}"
COMPARE="${MEETING_LLM_COMPARE:-0}"
OUT=""
PROJECT=""
NAME=""

usage() {
  cat <<'USAGE'
Usage: run-note-llm.sh --out FILE [--project PROJECT --name NAME] [--provider claude|codex] [--compare]

Reads a fully rendered meeting-note prompt from stdin and writes the selected
LLM provider's Markdown output to FILE.

Environment:
  MEETING_LLM_PROVIDER      claude or codex (default: claude)
  MEETING_LLM_COMPARE       1 to also run the non-selected provider and save both outputs
  MEETING_LLM_COMPARE_DIR   comparison output root (default: state/llm-comparisons)

  CLAUDE_OAUTH_RUN          claude-oauth-run path override
  CLAUDE_OAUTH_CLI          claude-oauth path override
  CLAUDE_MODEL              optional Claude Code model/alias passed via --model
  CLAUDE_TOOLS              Claude tools list (default: WebSearch)
  CLAUDE_MAX_BUDGET_USD     optional Claude Code --max-budget-usd value

  CODEX_BIN                 codex CLI path override
  CODEX_MODEL               optional Codex model passed via --model
  CODEX_REASONING_EFFORT    optional Codex reasoning effort (default: medium)
  CODEX_SEARCH              1 to enable Codex web search (default: 1)
  CODEX_SANDBOX             Codex sandbox mode (default: read-only)
  CODEX_APPROVAL_POLICY     Codex approval policy (default: never)
  CODEX_IGNORE_RULES        1 to ignore repo/user rules for note generation (default: 1)
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --out)
      OUT="${2:?--out requires a file}"
      shift
      ;;
    --project)
      PROJECT="${2:?--project requires a value}"
      shift
      ;;
    --name)
      NAME="${2:?--name requires a value}"
      shift
      ;;
    --provider)
      PROVIDER="${2:?--provider requires claude or codex}"
      shift
      ;;
    --compare)
      COMPARE=1
      ;;
    --no-compare)
      COMPARE=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [ -z "$OUT" ]; then
  echo "--out is required" >&2
  usage >&2
  exit 2
fi

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

validate_provider() {
  case "$1" in
    claude|codex) ;;
    *)
      echo "unsupported MEETING_LLM_PROVIDER: $1 (expected claude or codex)" >&2
      exit 2
      ;;
  esac
}

find_executable() {
  local override="$1"
  local command_name="$2"
  local fallback="$3"
  if [ -n "$override" ]; then
    if [ -x "$override" ] || command -v "$override" >/dev/null 2>&1; then
      echo "$override"
      return 0
    fi
    echo "$command_name not executable: $override" >&2
    return 1
  fi
  if command -v "$command_name" >/dev/null 2>&1; then
    command -v "$command_name"
    return 0
  fi
  if [ -n "$fallback" ] && [ -x "$fallback" ]; then
    echo "$fallback"
    return 0
  fi
  echo "$command_name not found" >&2
  return 1
}

ensure_claude_oauth() {
  local oauth_cli
  oauth_cli="$(find_executable "${CLAUDE_OAUTH_CLI:-}" "claude-oauth" "$HOME/.local/bin/claude-oauth")"
  if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    export CLAUDE_CODE_OAUTH_TOKEN="$("$oauth_cli" print-token)"
  fi
  if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    echo "CLAUDE_CODE_OAUTH_TOKEN not set — claude-oauth 프로파일에서 토큰을 가져올 수 없습니다." >&2
    exit 1
  fi
}

run_claude() {
  local prompt_file="$1"
  local output_file="$2"
  local claude_run
  claude_run="$(find_executable "${CLAUDE_OAUTH_RUN:-}" "claude-oauth-run" "$HOME/.local/bin/claude-oauth-run")"
  ensure_claude_oauth

  local args=(--dangerously-skip-permissions -p)
  if [ -n "${CLAUDE_MODEL:-}" ]; then
    args+=(--model "$CLAUDE_MODEL")
  fi
  local claude_tools="${CLAUDE_TOOLS-WebSearch}"
  if [ -n "$claude_tools" ]; then
    args+=(--tools "$claude_tools")
  else
    args+=(--tools "")
  fi
  if [ -n "${CLAUDE_MAX_BUDGET_USD:-}" ]; then
    args+=(--max-budget-usd "$CLAUDE_MAX_BUDGET_USD")
  fi

  env -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY -u CLAUDE_API_KEY \
    "$claude_run" "${args[@]}" < "$prompt_file" > "$output_file"
}

run_codex() {
  local prompt_file="$1"
  local output_file="$2"
  local codex_bin
  codex_bin="$(find_executable "${CODEX_BIN:-}" "codex" "")"

  local last_message
  local log_file
  last_message="$(mktemp)"
  log_file="$(mktemp)"
  local top_args=(
    --ask-for-approval "${CODEX_APPROVAL_POLICY:-never}"
    --sandbox "${CODEX_SANDBOX:-read-only}"
  )
  if truthy "${CODEX_SEARCH:-1}"; then
    top_args+=(--search)
  fi
  if [ -n "${CODEX_REASONING_EFFORT:-medium}" ]; then
    top_args+=(-c "model_reasoning_effort=\"${CODEX_REASONING_EFFORT:-medium}\"")
  fi

  local exec_args=(
    exec
    --ephemeral
    --skip-git-repo-check
    -C "$BASE"
    --color never
    -o "$last_message"
  )
  if truthy "${CODEX_IGNORE_RULES:-1}"; then
    exec_args+=(--ignore-rules)
  fi
  if [ -n "${CODEX_MODEL:-}" ]; then
    exec_args+=(-m "$CODEX_MODEL")
  fi
  exec_args+=(-)

  if ! "$codex_bin" "${top_args[@]}" "${exec_args[@]}" < "$prompt_file" > "$log_file" 2>&1; then
    cat "$log_file" >&2
    rm -f "$last_message" "$log_file"
    return 1
  fi
  cat "$last_message" > "$output_file"
  rm -f "$last_message" "$log_file"
}

run_provider() {
  local provider="$1"
  local prompt_file="$2"
  local output_file="$3"
  case "$provider" in
    claude) run_claude "$prompt_file" "$output_file" ;;
    codex) run_codex "$prompt_file" "$output_file" ;;
  esac
}

other_provider() {
  case "$1" in
    claude) echo "codex" ;;
    codex) echo "claude" ;;
  esac
}

validate_provider "$PROVIDER"

prompt_file="$(mktemp)"
selected_output="$(mktemp)"
trap 'rm -f "$prompt_file" "$selected_output"' EXIT

cat > "$prompt_file"

mkdir -p "$(dirname "$OUT")"
run_provider "$PROVIDER" "$prompt_file" "$selected_output"
cp "$selected_output" "$OUT"

if truthy "$COMPARE"; then
  compare_dir="${MEETING_LLM_COMPARE_DIR:-$BASE/state/llm-comparisons}"
  if [ -n "$PROJECT" ] && [ -n "$NAME" ]; then
    compare_dir="$compare_dir/$PROJECT/$NAME"
  fi
  mkdir -p "$compare_dir"
  cp "$selected_output" "$compare_dir/$PROVIDER.md"
  echo "$PROVIDER" > "$compare_dir/selected-provider.txt"

  candidate="$(other_provider "$PROVIDER")"
  if ! run_provider "$candidate" "$prompt_file" "$compare_dir/$candidate.md"; then
    echo "provider failed: $candidate" > "$compare_dir/$candidate.err"
  fi
  echo "llm comparison saved: $compare_dir"
fi
