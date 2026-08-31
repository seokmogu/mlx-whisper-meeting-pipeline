#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

OUT=""
OUTPUT_TYPE="note"

usage() {
  cat <<'USAGE'
Usage: run-note-llm.sh --out FILE [--output-type note|json]

Reads a fully rendered request payload from stdin and writes the Codex output
to FILE. The default contract is a Markdown meeting note; --output-type json
is reserved for transcript correction proposals.

Environment:
  CODEX_BIN                 codex CLI path override
  CODEX_MODEL               frontier, default, or a full Codex model name
  CODEX_REASONING_EFFORT    highest, default, low, medium, high, or xhigh
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
    --output-type)
      OUTPUT_TYPE="${2:?--output-type requires note or json}"
      shift
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

find_executable() {
  local override="$1"
  local command_name="$2"
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
  echo "$command_name not found" >&2
  return 1
}

config_value() {
  local key="$1"
  local config="${CODEX_HOME:-$HOME/.codex}/config.toml"
  [ -f "$config" ] || return 0
  awk -F'"' -v key="$key" '$0 ~ "^[[:space:]]*" key "[[:space:]]*=" {print $2; exit}' "$config"
}

resolve_codex_model() {
  local model="${CODEX_MODEL-frontier}"
  case "$model" in
    ""|auto|default|config)
      return 0
      ;;
    highest|best|frontier)
      local frontier_model
      frontier_model="${OMX_DEFAULT_FRONTIER_MODEL:-}"
      if [ -z "$frontier_model" ]; then
        frontier_model="$(config_value "OMX_DEFAULT_FRONTIER_MODEL")"
      fi
      if [ -z "$frontier_model" ]; then
        frontier_model="$(config_value "model")"
      fi
      echo "$frontier_model"
      ;;
    *)
      echo "$model"
      ;;
  esac
}

resolve_codex_effort() {
  local effort="${CODEX_REASONING_EFFORT-highest}"
  case "$effort" in
    ""|auto|default|config)
      return 0
      ;;
    highest|best|max)
      echo "xhigh"
      ;;
    *)
      echo "$effort"
      ;;
  esac
}

run_codex() {
  local prompt_file="$1"
  local output_file="$2"
  local codex_bin
  codex_bin="$(find_executable "${CODEX_BIN:-}" "codex")"

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
  local codex_effort
  codex_effort="$(resolve_codex_effort)"
  if [ -n "$codex_effort" ]; then
    top_args+=(-c "model_reasoning_effort=\"$codex_effort\"")
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
  local codex_model
  codex_model="$(resolve_codex_model)"
  if [ -n "$codex_model" ]; then
    exec_args+=(-m "$codex_model")
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

validate_note_output() {
  local output_file="$1"
  "$BASE/sh/validate_meeting_note.py" "$output_file"
}

case "$OUTPUT_TYPE" in
  note|json) ;;
  *)
    echo "unsupported output type: $OUTPUT_TYPE (expected note or json)" >&2
    exit 2
    ;;
esac

prompt_file="$(mktemp)"
codex_prompt="$(mktemp)"
selected_output="$(mktemp)"
validation_log="$(mktemp)"
repair_prompt="$(mktemp)"
repaired_output="$(mktemp)"
trap 'rm -f "$prompt_file" "$codex_prompt" "$selected_output" "$validation_log" "$repair_prompt" "$repaired_output"' EXIT

cat > "$prompt_file"
search_available="no"
if truthy "${CODEX_SEARCH:-1}"; then
  search_available="yes"
fi
{
  cat <<CONTEXT
런타임 LLM context:
- provider: codex
- web search available: $search_available
- web search available이 no이면 검색을 수행한 것처럼 쓰지 마세요. "웹검색 결과", "검색 결과", "공개 자료 확인", "web_search 결과" 같은 표현을 금지합니다.
- web search available이 yes여도 실제 도구 호출 없이 외부 검증을 했다고 쓰지 마세요.

CONTEXT
  cat "$prompt_file"
} > "$codex_prompt"

mkdir -p "$(dirname "$OUT")"
run_codex "$codex_prompt" "$selected_output"
if [ "$OUTPUT_TYPE" = "note" ]; then
  if ! validate_note_output "$selected_output" > "$validation_log" 2>&1; then
    cat "$validation_log" >&2
    echo "meeting note contract failed; retrying once with validator feedback" >&2
    {
      cat "$codex_prompt"
      cat <<'REPAIR'

---
첫 번째 회의록 초안이 결정적 계약 검증에 실패했습니다.
아래 검증 오류만 고치되, 원문 전사의 사실·수치·날짜·이름·부정·확정성은 바꾸지 마세요.
수정된 전체 한국어 Markdown 회의록만 출력하세요. 설명이나 완료 보고는 출력하지 마세요.

검증 오류:
REPAIR
      cat "$validation_log"
      cat <<'REPAIR'

첫 번째 초안:
REPAIR
      cat "$selected_output"
    } > "$repair_prompt"
    run_codex "$repair_prompt" "$repaired_output"
    if ! validate_note_output "$repaired_output" > "$validation_log" 2>&1; then
      cat "$validation_log" >&2
      echo "meeting note contract failed after one repair attempt" >&2
      exit 1
    fi
    cp "$repaired_output" "$selected_output"
  fi
else
  "$BASE/sh/apply_transcript_corrections.py" check-proposal "$selected_output"
fi
if [ "$OUTPUT_TYPE" = "note" ] && [ -s "$BASE/glossary/identity_ledger.md" ] && [ -x "$BASE/sh/check_identity_ledger_usage.py" ]; then
  if ! "$BASE/sh/check_identity_ledger_usage.py" \
      "$selected_output" \
      "$BASE/glossary/identity_ledger.md" \
      --roster "$BASE/glossary/employee_roster.tsv" \
      --min-count "${MEETING_IDENTITY_GATE_MIN_COUNT:-7}"; then
    echo "identity-usage check flagged residual high-confidence variants (non-blocking; note kept)" >&2
  fi
fi
cp "$selected_output" "$OUT"
