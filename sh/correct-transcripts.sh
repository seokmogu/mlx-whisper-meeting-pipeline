#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
if [ -f "$BASE/.env" ]; then
  set -a; source "$BASE/.env"; set +a
fi
TRANSCRIPT_DIR="$BASE/transcripts"
CORRECTED_DIR="${MEETING_CORRECTED_TRANSCRIPT_DIR:-$BASE/state/corrected-transcripts}"
CORRECTION_DIR="${MEETING_TRANSCRIPT_CORRECTION_DIR:-$BASE/state/transcript-corrections}"
ATTENDEES_DIR="${MEETING_ATTENDEES_DIR:-$BASE/state/meeting-attendees}"
VOICE_TITLE_DIR="${VOICE_MEMO_TITLE_DIR:-$BASE/state/voice-memo-titles}"
FORCE=0
DRY_RUN=0
ONLY=""

usage() {
  cat <<'USAGE'
Usage: correct-transcripts.sh [--force] [--only PROJECT/NAME] [--dry-run]

Creates a verified, lexical-only corrected transcript derivative. The original
transcript is never modified. Existing notes are not backfilled unless --force
is given, which prevents first-run correction of the entire archive.

Options:
  --force          Regenerate a correction even when a note/artifact exists.
  --only TARGET    Restrict to NAME, PROJECT/NAME, NAME.txt, or PROJECT/NAME.txt.
  --dry-run        Report targets without calling an LLM or writing artifacts.
  -h, --help       Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --force)
      FORCE=1
      ;;
    --only)
      ONLY="${2:?--only requires a target}"
      shift
      ;;
    --dry-run)
      DRY_RUN=1
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

normalize_target() {
  local target="$1"
  target="${target%.txt}"
  target="${target%.md}"
  echo "$target"
}

matches_only() {
  local project="$1"
  local name="$2"
  [ -z "$ONLY" ] && return 0
  local target
  target="$(normalize_target "$ONLY")"
  [ "$target" = "$name" ] || [ "$target" = "$project/$name" ]
}

emit_optional_file() {
  local label="$1"
  local path="$2"
  if [ -s "$path" ]; then
    echo "$label"
    cat "$path"
    echo ""
  fi
}

read -r -a PROJECTS <<<"${MEETING_PROJECTS:-worxphere}"
corrected_count=0
skipped_count=0
failed_count=0

shopt -s nullglob
for project in "${PROJECTS[@]}"; do
  for raw in "$TRANSCRIPT_DIR/$project"/*.txt; do
    name="$(basename "$raw" .txt)"
    if ! matches_only "$project" "$name"; then
      continue
    fi

    corrected="$CORRECTED_DIR/$project/$name.txt"
    artifact_dir="$CORRECTION_DIR/$project"
    proposal="$artifact_dir/$name.proposal.json"
    manifest="$artifact_dir/$name.json"
    ledger_file="$artifact_dir/$name.ledger.md"
    attendees="$ATTENDEES_DIR/$project/$name.txt"
    voice_title="$VOICE_TITLE_DIR/$project/$name.txt"
    note="$BASE/notes/$project/$name.md"

    if [ "$FORCE" -eq 0 ] && [ -f "$note" ] && [ ! -f "$corrected" ]; then
      echo "skip correction for existing note (use --force to backfill): $project/$name"
      skipped_count=$((skipped_count + 1))
      continue
    fi
    if [ "$FORCE" -eq 0 ] && [ -f "$corrected" ] && [ -f "$manifest" ]; then
      if "$BASE/sh/apply_transcript_corrections.py" verify \
          --raw "$raw" --corrected "$corrected" --manifest "$manifest" --quiet; then
        skipped_count=$((skipped_count + 1))
        continue
      fi
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
      echo "dry-run correct transcript: $project/$name"
      corrected_count=$((corrected_count + 1))
      continue
    fi

    mkdir -p "$(dirname "$corrected")" "$artifact_dir"
    "$BASE/sh/build_identity_ledger.py" \
      "$BASE/notes/$project" "$ledger_file" \
      --transcripts-dir "$BASE/transcripts/$project" \
      --before-name "$name.md" \
      --max-entries "${MEETING_TRANSCRIPT_CORRECTION_LEDGER_MAX_ENTRIES:-2000}" \
      >/dev/null
    phonetic_candidates="$(mktemp)"
    relevant_ledger="$(mktemp)"
    if [ -s "$BASE/glossary/employee_roster.tsv" ]; then
      "$BASE/sh/phonetic_name_candidates.py" \
        "$raw" "$BASE/glossary/employee_roster.tsv" "$phonetic_candidates" \
        2>/dev/null || true
    fi
    if [ -s "$ledger_file" ]; then
      "$BASE/sh/apply_transcript_corrections.py" show-relevant-ledger \
        --raw "$raw" --ledger "$ledger_file" > "$relevant_ledger" || true
    fi

    echo "proposing transcript corrections: $project/$name (codex)"
    if ! {
      cat <<'PROMPT'
당신은 한국어 STT 녹취록의 **제한적 교정 패치 제안기**입니다.

목표는 회의록을 작성하거나 문장을 매끄럽게 다시 쓰는 것이 아닙니다. 원본의 타임스탬프, 화자, 발화 순서, 문장 구조를 그대로 두고, 제공된 근거로 확정 가능한 짧은 인명·제품명·조직명·약어만 제안합니다.

절대 규칙:
- JSON 객체 하나만 출력합니다. 코드펜스와 설명문을 쓰지 않습니다.
- 타임스탬프, 화자 라벨, 발화 순서, 줄 수를 변경하지 않습니다.
- 문법, 조사, 말투, 반복, 어색한 문장, 긴 훼손 구간을 고치지 않습니다.
- transcript에 없는 단어를 문맥상 그럴듯하다는 이유로 복원하지 않습니다.
- 숫자, 날짜, 시간, 금액, 기한, 부정어(안/못/없다/아니다), 약속·담당·완료 표현을 바꾸지 않습니다.
- 이전 회의 참석자나 주제 연속성만으로 인물을 정하지 않습니다.
- 인명은 누적 사전의 동일 변형이 충분히 반복됐거나, 현재 참석자/직원명단의 이름과 발음이 매우 가까울 때만 제안합니다.
- 긴 문장 전체가 아닌 정확히 한 번 등장하는 최소 문자열만 before로 지정합니다.
- 확신도가 0.92 미만이면 제안하지 않습니다.
- 안전한 교정이 없으면 {"version":1,"patches":[]}를 출력합니다.

허용 category: person_name, proper_noun, organization, acronym

출력 스키마:
{"version":1,"patches":[{"line":"L001","before":"원문 일부","after":"교정 문자열","category":"proper_noun","confidence":0.98,"reason":"제공된 근거와 현재 발화 문맥"}]}

person_name의 after에는 소속/직책을 붙이지 말고 이름과 원문 호칭만 유지합니다. 예: `성모님` → `구석모님`.
누적 사전의 제품/조직/약어 target은 표시된 정정 문자열을 사용합니다.

---
현재 회의 근거:
PROMPT
      emit_optional_file "사용자 확정 참석자:" "$attendees"
      emit_optional_file "Voice Memo 제목(보조 근거):" "$voice_title"
      emit_optional_file "현재 transcript에 실제 등장한 누적 확정 표기 후보:" "$relevant_ledger"
      emit_optional_file "현재 transcript에서 계산한 발음 유사 직원 후보:" "$phonetic_candidates"
      cat <<'PROMPT'
---
줄 번호가 부여된 원본 transcript:
PROMPT
      nl -ba -w4 -s $'\t' "$raw" | sed -E 's/^[[:space:]]*([0-9]+)/L\1/'
    } | CODEX_SEARCH=0 \
      "$BASE/sh/run-note-llm.sh" \
        --out "$proposal" \
        --output-type json; then
      echo "transcript correction proposal failed; raw transcript will be used: $project/$name" >&2
      rm -f "$phonetic_candidates" "$relevant_ledger"
      failed_count=$((failed_count + 1))
      continue
    fi
    rm -f "$phonetic_candidates" "$relevant_ledger"

    apply_args=(
      apply
      --raw "$raw"
      --proposal "$proposal"
      --corrected "$corrected"
      --manifest "$manifest"
      --ledger "$ledger_file"
      --roster "$BASE/glossary/employee_roster.tsv"
      --attendees "$attendees"
      --min-confidence "${MEETING_TRANSCRIPT_CORRECTION_MIN_CONFIDENCE:-0.92}"
      --person-ledger-min-count "${MEETING_TRANSCRIPT_CORRECTION_PERSON_LEDGER_MIN_COUNT:-5}"
      --attendee-phonetic-threshold "${MEETING_TRANSCRIPT_CORRECTION_ATTENDEE_PHONETIC_THRESHOLD:-0.72}"
      --roster-phonetic-threshold "${MEETING_TRANSCRIPT_CORRECTION_ROSTER_PHONETIC_THRESHOLD:-0.86}"
      --max-patch-chars "${MEETING_TRANSCRIPT_CORRECTION_MAX_PATCH_CHARS:-60}"
      --max-edit-ratio "${MEETING_TRANSCRIPT_CORRECTION_MAX_EDIT_RATIO:-0.05}"
    )
    if ! "$BASE/sh/apply_transcript_corrections.py" "${apply_args[@]}"; then
      echo "transcript correction validation failed; raw transcript will be used: $project/$name" >&2
      failed_count=$((failed_count + 1))
      continue
    fi
    corrected_count=$((corrected_count + 1))
  done
done

echo "---"
echo "transcript corrections: corrected=$corrected_count, skipped=$skipped_count, failed=$failed_count"
exit 0
