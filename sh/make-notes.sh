#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
TRANSCRIPT_DIR="$BASE/transcripts"
NOTES_DIR="$BASE/notes"
MEETING_NOTES_SKILL="${MEETING_NOTES_SKILL:-$BASE/skills/meeting-minutes/SKILL.md}"
MEETING_PREVIOUS_NOTES_LIMIT="${MEETING_PREVIOUS_NOTES_LIMIT:-3}"
MEETING_PREVIOUS_NOTE_MAX_LINES="${MEETING_PREVIOUS_NOTE_MAX_LINES:-160}"
FORCE=0
DRY_RUN=0
ONLY=""
LLM_PROVIDER_OVERRIDE=""
LLM_COMPARE_OVERRIDE=""

usage() {
  cat <<'USAGE'
Usage: make-notes.sh [--force] [--only PROJECT/NAME] [--provider claude|codex] [--compare-llm] [--dry-run]

Generates Markdown meeting notes from transcripts.

Options:
  --force          Regenerate existing notes. Existing note is backed up first.
  --only TARGET    Process only NAME, PROJECT/NAME, NAME.txt, or PROJECT/NAME.txt.
  --provider NAME  Override MEETING_LLM_PROVIDER for this run (claude or codex).
  --compare-llm    Also run the non-selected provider and save comparison outputs under state/.
  --no-compare-llm Disable comparison for this run.
  --dry-run        Report what would happen without calling an LLM or writing notes.
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
    --provider)
      LLM_PROVIDER_OVERRIDE="${2:?--provider requires claude or codex}"
      shift
      ;;
    --compare-llm)
      LLM_COMPARE_OVERRIDE=1
      ;;
    --no-compare-llm)
      LLM_COMPARE_OVERRIDE=0
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
  local proj="$1"
  local name="$2"
  [ -z "$ONLY" ] && return 0
  local target
  target="$(normalize_target "$ONLY")"
  [ "$target" = "$name" ] || [ "$target" = "$proj/$name" ]
}

backup_note() {
  local proj="$1"
  local name="$2"
  local out="$3"
  local backup_dir="$BASE/state/note-backups/$proj/$(date '+%Y%m%d-%H%M%S')"
  mkdir -p "$backup_dir"
  cp -p "$out" "$backup_dir/$name.md"
  echo "backed up existing note: $backup_dir/$name.md"
}

emit_previous_note_context() {
  local proj="$1"
  local current_name="$2"
  local out_dir="$3"
  local limit="$MEETING_PREVIOUS_NOTES_LIMIT"
  local max_lines="$MEETING_PREVIOUS_NOTE_MAX_LINES"

  case "$limit" in
    ""|0)
      return 0
      ;;
  esac
  [ -d "$out_dir" ] || return 0

  local tmp
  tmp="$(mktemp)"
  find "$out_dir" -maxdepth 1 -type f -name '*.md' ! -name "$current_name.md" -print | sort | tail -n "$limit" > "$tmp"
  if [ ! -s "$tmp" ]; then
    rm -f "$tmp"
    return 0
  fi

  cat <<PREV

---
이전 회의록 참고자료:
- 같은 project($proj)의 최근 회의록에서 후속 액션/결정/리스크 판단에 필요한 섹션만 발췌했다.
- 아래 내용은 Previous Action Follow-up, 반복 이슈, 중복 액션 판단에만 사용한다.

PREV

  while IFS= read -r prev; do
    [ -f "$prev" ] || continue
    echo "### $(basename "$prev")"
    awk -v max_lines="$max_lines" '
      BEGIN { capture = 0; count = 0 }
      /^## / {
        capture = ($0 ~ /^## ([0-9]+[.] )?(핵심 요약|요약|주요 결정|결정사항|Agenda Evaluation|Previous Action Follow-up|Action Items|액션 아이템|Task Handoff|리스크|다음 회의)/)
      }
      capture && count < max_lines {
        print
        count++
      }
    ' "$prev"
    echo ""
  done < "$tmp"

  rm -f "$tmp"
}

if [ -n "$LLM_PROVIDER_OVERRIDE" ]; then
  export MEETING_LLM_PROVIDER="$LLM_PROVIDER_OVERRIDE"
fi
if [ -n "$LLM_COMPARE_OVERRIDE" ]; then
  export MEETING_LLM_COMPARE="$LLM_COMPARE_OVERRIDE"
fi

if [ ! -f "$MEETING_NOTES_SKILL" ]; then
  echo "meeting notes skill not found: $MEETING_NOTES_SKILL" >&2
  exit 1
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "dry-run skill: $MEETING_NOTES_SKILL"
fi

read -r -a PROJECTS <<<"${MEETING_PROJECTS:-worxphere}"

made=0
skipped=0
overwritten=0

shopt -s nullglob
for proj in "${PROJECTS[@]}"; do
  in_dir="$TRANSCRIPT_DIR/$proj"
  out_dir="$NOTES_DIR/$proj"
  [ -d "$in_dir" ] || continue
  mkdir -p "$out_dir"

  for transcript in "$in_dir"/*.txt; do
    name="$(basename "$transcript" .txt)"
    if ! matches_only "$proj" "$name"; then
      continue
    fi
    out="$out_dir/$name.md"

    if [ -f "$out" ] && [ "$FORCE" -eq 0 ]; then
      skipped=$((skipped + 1))
      continue
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
      if [ -f "$out" ] && [ "$FORCE" -eq 1 ]; then
        echo "dry-run overwrite: $proj/$name"
        overwritten=$((overwritten + 1))
      else
        echo "dry-run make notes: $proj/$name"
        made=$((made + 1))
      fi
      continue
    fi

    if [ -f "$out" ] && [ "$FORCE" -eq 1 ]; then
      backup_note "$proj" "$name" "$out"
      overwritten=$((overwritten + 1))
    fi

    echo "making notes: $proj/$name"

    {
      cat <<'PROMPT'
다음은 한국어 회의 녹취록을 운영 가능한 회의록으로 정리하는 작업입니다.

반드시 아래 `SKILL.md`를 작성 규칙의 source of truth로 사용하세요.
프론트매터는 메타데이터이고, 본문 지침과 Output Contract를 우선합니다.

출력 원칙:
- 한국어 Markdown 본문만 출력
- 서론/사족 금지
- 원문 transcript 전체를 부록으로 붙이지 않음
- 없는 정보는 만들지 말고 `확인 필요`로 표시
- 회의가 45분 이상이고 내용이 충분하면 짧은 요약 노트로 압축하지 말고 상세 운영 회의록으로 작성

---
사용할 회의록 작성 스킬:
PROMPT
      cat "$MEETING_NOTES_SKILL"
      cat <<'PROMPT'

---
현재 회의 메타데이터:
PROMPT
      echo "- Project: \`$proj\`"
      echo "- Source transcript: \`$transcript\`"
      echo "- Output note: \`$out\`"
      echo "- Generated label: \`AI 추정\`"
      emit_previous_note_context "$proj" "$name" "$out_dir"
      if [ -s "$BASE/glossary/employee_roster.tsv" ]; then
        cat <<'ROSTER'

---
**이름 정규화 — 직원 디렉토리**
형식: `이름<TAB>소속팀<TAB>직책`. 전사의 "~님" 호칭, 짧은 이름, 유사 발음을 이 명부와 매칭해 인물을 특정한다.
- 이름이 명부에서 1명으로 확정되고 문맥이 맞을 때만 `이름(소속팀, 직책)`으로 정규화
- 동명이인이거나 소속/역할 문맥이 맞지 않으면 원문을 유지하고 `## 검증 필요`에 남김
- 액션 아이템 담당자는 명시 발화가 있을 때만 직원명으로 작성. 회의 흐름상 추정되는 사람은 담당자로 만들지 않음
- 이메일, 전화번호, 사번은 출력하지 않음
- 정정 시 `## 검증 완료`에 `"민수님" → **김민수(Product팀, PO)**` 형식으로 기록

직원 디렉토리:
ROSTER
        cat "$BASE/glossary/employee_roster.tsv"
      elif [ -s "$BASE/glossary/roster.tsv" ]; then
        cat <<'ROSTER'

---
**이름 정규화 — 워크스페이스 멤버 명부**
형식: `이름<TAB>이메일`. 전사의 "~님" 호칭이나 짧은 이름을 이 명부와 매칭해 풀네임으로 정정.
- 호칭에서 "님" 제거 → 명부의 이름(대개 `_` 앞부분)과 유사도 비교 → 일치 확실할 때만 대체
- 정정 시 `## 검증 완료`에 `"민수님" → **김민수_제품팀**` 형식으로 기록
- 명부에 없거나 동명이인/매칭 불확실 → 원문 유지
- 명부는 참고용이므로 매칭이 애매하면 임의 추정 금지

명부:
ROSTER
        cat "$BASE/glossary/roster.tsv"
      fi
      cat <<'TAIL'

---
녹취록:
TAIL
      cat "$transcript"
    } | "$BASE/sh/run-note-llm.sh" \
        --out "$out" \
        --project "$proj" \
        --name "$name"

    made=$((made + 1))
  done
done

echo "---"
echo "made: $made, overwritten: $overwritten, skipped (already exists): $skipped"
projects_csv="$(IFS=,; echo "${PROJECTS[*]}")"
echo "destination: $NOTES_DIR/{$projects_csv}"
