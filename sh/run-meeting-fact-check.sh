#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SKILL="${MEETING_FACT_CHECK_SKILL:-$BASE/skills/objective-fact-check/SKILL.md}"
PYTHON_TOOL="$BASE/sh/meeting_fact_check.py"
CANDIDATE_SCHEMA="$BASE/sh/objective-fact-check-candidates.schema.json"
EVIDENCE_SCHEMA="$BASE/sh/objective-fact-check-evidence.schema.json"
AUDIO_REVIEW_SCHEMA="$BASE/sh/objective-fact-check-audio-review.schema.json"
AUDIO_RETRANSCRIBER="$BASE/sh/retranscribe_fact_check_segments.py"
NOTE=""
TRANSCRIPT=""
AUDIO=""
OUT_JSON=""

usage() {
  cat <<'USAGE'
Usage: run-meeting-fact-check.sh --note FILE --transcript FILE [--audio FILE] --out-json FILE

Runs privacy-separated objective claim extraction and public web verification,
stores the JSON audit artifacts, and appends section 6 to the meeting note.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --note) NOTE="${2:?--note requires a file}"; shift ;;
    --transcript) TRANSCRIPT="${2:?--transcript requires a file}"; shift ;;
    --audio) AUDIO="${2:?--audio requires a file}"; shift ;;
    --out-json) OUT_JSON="${2:?--out-json requires a file}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [ -z "$NOTE" ] || [ -z "$TRANSCRIPT" ] || [ -z "$OUT_JSON" ]; then
  usage >&2
  exit 2
fi
for required in "$NOTE" "$TRANSCRIPT" "$SKILL" "$PYTHON_TOOL" "$CANDIDATE_SCHEMA" "$EVIDENCE_SCHEMA" "$AUDIO_REVIEW_SCHEMA" "$AUDIO_RETRANSCRIBER"; do
  if [ ! -f "$required" ]; then
    echo "objective fact-check input missing: $required" >&2
    exit 1
  fi
done

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

config_value() {
  local key="$1"
  local config="${CODEX_HOME:-$HOME/.codex}/config.toml"
  [ -f "$config" ] || return 0
  awk -F'"' -v key="$key" '$0 ~ "^[[:space:]]*" key "[[:space:]]*=" {print $2; exit}' "$config"
}

resolve_model() {
  local model="${MEETING_FACT_CHECK_MODEL:-${CODEX_MODEL:-gpt-5.6-sol}}"
  case "$model" in
    ""|auto|default|config) return 0 ;;
    highest|best|frontier)
      local configured
      configured="${OMX_DEFAULT_FRONTIER_MODEL:-$(config_value OMX_DEFAULT_FRONTIER_MODEL)}"
      [ -n "$configured" ] || configured="$(config_value model)"
      printf '%s' "$configured"
      ;;
    *) printf '%s' "$model" ;;
  esac
}

resolve_effort() {
  local effort="${MEETING_FACT_CHECK_REASONING_EFFORT:-high}"
  case "$effort" in
    ""|auto|default|config) return 0 ;;
    highest|best|max) printf 'xhigh' ;;
    *) printf '%s' "$effort" ;;
  esac
}

CODEX_BIN="${CODEX_BIN:-codex}"
if ! command -v "$CODEX_BIN" >/dev/null 2>&1; then
  echo "codex CLI not found: $CODEX_BIN" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUT_JSON")"
prefix="${OUT_JSON%.json}"
candidates="$prefix.candidates.json"
search_input="$prefix.search-input.json"
evidence="$prefix.evidence.json"
extract_events="$prefix.extract.events.jsonl"
search_events="$prefix.search.events.jsonl"
extract_stderr="$prefix.extract.stderr.log"
search_stderr="$prefix.search.stderr.log"
audio_segments="$prefix.audio-segments.json"
audio_review="$prefix.audio-review.json"
audio_events="$prefix.audio-review.events.jsonl"
audio_stderr="$prefix.audio-review.stderr.log"
extract_prompt="$(mktemp)"
search_prompt="$(mktemp)"
audio_prompt="$(mktemp)"
note_without_fact_check="$(mktemp)"
workspace="$(mktemp -d)"
trap 'rm -f "$extract_prompt" "$search_prompt" "$audio_prompt" "$note_without_fact_check"; rmdir "$workspace" 2>/dev/null || true' EXIT
python3 "$PYTHON_TOOL" strip-note --input "$NOTE" --output "$note_without_fact_check"

fallback() {
  local reason="$1"
  echo "objective fact check fallback: $reason" >&2
  python3 "$PYTHON_TOOL" fallback \
    --note "$NOTE" \
    --transcript "$TRANSCRIPT" \
    --output "$OUT_JSON" \
    --reason "$reason"
  exit 0
}

run_stage() {
  local prompt="$1"
  local schema="$2"
  local output="$3"
  local events="$4"
  local stderr_log="$5"
  local enable_search="$6"
  local args=(
    --ask-for-approval "${CODEX_APPROVAL_POLICY:-never}"
    --sandbox "${CODEX_SANDBOX:-read-only}"
  )
  if [ "$enable_search" = "1" ]; then
    args+=(--search)
  fi
  local effort
  effort="$(resolve_effort)"
  [ -z "$effort" ] || args+=(-c "model_reasoning_effort=\"$effort\"")
  args+=(
    exec --ephemeral --skip-git-repo-check --ignore-rules
    -C "$workspace" --color never --json
    --output-schema "$schema" -o "$output"
  )
  local model
  model="$(resolve_model)"
  [ -z "$model" ] || args+=(-m "$model")
  args+=(-)
  "$CODEX_BIN" "${args[@]}" < "$prompt" > "$events" 2> "$stderr_log"
}

{
  cat <<'PROMPT'
다음 입력에서 객관적으로 판정 가능한 고위험 명제와 상충하는 사실 발언을 추출하세요.

Mode: Extract
- 웹검색을 사용하지 마세요.
- 의견, 제안, 예측, 가치 판단, 회의에서 새로 정한 결정은 제외하세요.
- 화자와 타임스탬프를 보존하세요.
- 공개 웹검색 후보는 사람명·사내 조직·고객·내부 코드명·비공개 문맥을 제거한 public_claim으로 바꾸세요.
- 내부 정본이 필요한 명제는 internal_authority, 객관적 기준 없이 화자끼리 충돌하면 speaker_conflict로 분류하세요.
- internal_authority 후보에는 판정에 필요한 구체 사내 정본을 required_evidence에 적으세요.
- 최대 8개만 출력하고 억지로 채우지 마세요.
- 최종 응답은 제공된 JSON schema를 만족하는 JSON 하나만 출력하세요.

사용할 팩트체크 스킬:
PROMPT
  cat "$SKILL"
  printf '\n---\n회의록:\n'
  cat "$note_without_fact_check"
  printf '\n---\n타임스탬프 전사:\n'
  cat "$TRANSCRIPT"
} > "$extract_prompt"

if ! run_stage "$extract_prompt" "$CANDIDATE_SCHEMA" "$candidates" "$extract_events" "$extract_stderr" 0; then
  fallback "객관 명제 추출 에이전트 실행 실패"
fi
if ! python3 "$PYTHON_TOOL" validate-candidates "$candidates"; then
  fallback "객관 명제 후보 계약 검증 실패"
fi
python3 "$PYTHON_TOOL" prepare-search "$candidates" "$search_input"

search_count="$(jq '.candidates | length' "$search_input")"
web_search_observed=0
if [ "$search_count" -gt 0 ] && truthy "${MEETING_FACT_CHECK_WEB_SEARCH:-1}"; then
  {
    cat <<PROMPT
다음 공개 검색용 명제 ${search_count}개를 실제 웹검색으로 검증하세요.

Mode: Verify
- 이 입력에는 원문 회의나 사람명이 없습니다. 주어진 public_claim만 검색하세요.
- 각 후보마다 실제 웹검색을 수행하세요.
- 결정론적 계산, 표준기관, 공공기관, 공식 제품 문서, 원본 공개 데이터 순으로 근거를 찾으세요.
- 검색결과 페이지가 아니라 사실을 직접 뒷받침하는 HTTPS URL을 쓰세요.
- corrected_fact, explanation, why_relevant는 자연스러운 한국어로 작성하세요. 공식 source title과 locator만 원문 언어를 유지할 수 있습니다.
- STT 위험 여부와 무관하게 공개 근거가 지지하는 사실관계만 verified, contradicted, partially_verified, not_assessable 중 하나로 판정하세요. STT 확인은 후속 오디오 단계가 담당합니다.
- 객관 사실과 충돌한다고 확정하려면 high confidence, 올바른 사실, 틀린 이유, 직접 출처가 모두 필요합니다.
- 출처가 약하거나 버전·범위가 다르면 partially_verified 또는 not_assessable을 사용하세요.
- 최종 응답은 제공된 JSON schema를 만족하는 JSON 하나만 출력하세요.

사용할 팩트체크 스킬:
PROMPT
    cat "$SKILL"
    printf '\n---\n공개 검색용 후보:\n'
    cat "$search_input"
  } > "$search_prompt"
  if ! run_stage "$search_prompt" "$EVIDENCE_SCHEMA" "$evidence" "$search_events" "$search_stderr" 1; then
    fallback "웹검색 검증 에이전트 실행 실패"
  fi
  if rg -q '"type"[[:space:]]*:[[:space:]]*"web_search"' "$search_events"; then
    web_search_observed=1
  fi
else
  printf '%s\n' '{"version":1,"web_search_performed":false,"results":[]}' > "$evidence"
  : > "$search_events"
  : > "$search_stderr"
fi

if ! python3 "$PYTHON_TOOL" validate-evidence "$evidence" "$candidates"; then
  fallback "웹검색 근거 계약 검증 실패"
fi

printf '%s\n' '{"version":1,"results":[]}' > "$audio_review"
: > "$audio_events"
: > "$audio_stderr"
if [ -n "$AUDIO" ] && [ -s "$AUDIO" ] && [ -x "$BASE/.venv/bin/python" ]; then
  if "$BASE/.venv/bin/python" "$AUDIO_RETRANSCRIBER" \
      --audio "$AUDIO" \
      --candidates "$candidates" \
      --evidence "$evidence" \
      --output "$audio_segments"; then
    audio_count="$(jq '.items | length' "$audio_segments")"
    if [ "$audio_count" -gt 0 ]; then
      {
        cat <<PROMPT
다음은 STT 위험이 있는 객관 명제를 원본 오디오 구간에서 별도로 재전사한 결과입니다.

- 웹검색을 사용하지 마세요.
- claim_text가 retranscript_text에서 실제로 발화됐는지 의미 기준으로 비교하세요.
- 명제가 분명히 확인되면 confirmed, 다른 의미면 not_confirmed, 여전히 훼손됐으면 uncertain을 사용하세요.
- explanation과 supporting_text는 한국어로 작성하세요.
- supporting_text에는 판단에 직접 필요한 짧은 재전사 구절만 넣으세요.
- 최종 응답은 제공된 JSON schema를 만족하는 JSON 하나만 출력하세요.

오디오 구간 재전사 패킷:
PROMPT
        cat "$audio_segments"
      } > "$audio_prompt"
      if run_stage "$audio_prompt" "$AUDIO_REVIEW_SCHEMA" "$audio_review" "$audio_events" "$audio_stderr" 0; then
        if ! python3 "$PYTHON_TOOL" validate-audio-review "$audio_review" "$candidates"; then
          printf '%s\n' '{"version":1,"results":[]}' > "$audio_review"
        fi
      else
        printf '%s\n' '{"version":1,"results":[]}' > "$audio_review"
      fi
    fi
  else
    echo "objective fact-check audio re-transcription failed; keep STT review gate" >&2
  fi
fi

merge_args=(
  merge
  --candidates "$candidates"
  --evidence "$evidence"
  --note "$NOTE"
  --transcript "$TRANSCRIPT"
  --output "$OUT_JSON"
  --audio-review "$audio_review"
)
[ "$web_search_observed" -eq 0 ] || merge_args+=(--web-search-observed)
if ! python3 "$PYTHON_TOOL" "${merge_args[@]}"; then
  fallback "객관 명제와 웹 근거 병합 실패"
fi
python3 "$PYTHON_TOOL" apply --result "$OUT_JSON" --note "$NOTE"
echo "objective fact check: applied ($search_count web-search candidate(s), observed=$web_search_observed)"
