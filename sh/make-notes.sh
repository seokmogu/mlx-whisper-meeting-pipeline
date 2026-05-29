#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
TRANSCRIPT_DIR="$BASE/transcripts"
NOTES_DIR="$BASE/notes"
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

if [ -n "$LLM_PROVIDER_OVERRIDE" ]; then
  export MEETING_LLM_PROVIDER="$LLM_PROVIDER_OVERRIDE"
fi
if [ -n "$LLM_COMPARE_OVERRIDE" ]; then
  export MEETING_LLM_COMPARE="$LLM_COMPARE_OVERRIDE"
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
다음은 한국어 회의 녹취록입니다. 두 가지 형식 중 하나로 들어옵니다:
- 형식 A (WhisperX): 각 발화가 `[시작 - 끝] 화자:` 로 표기되며 화자는 A, B로 익명화됨
- 형식 B (Notion AI): 상단에 `[Notion AI 전사 ...]` 헤더가 있고 화자/타임스탬프 없이 발화 단위로 줄바꿈된 평문

이를 바탕으로 정리된 미팅노트를 마크다운 형식으로 작성해주세요.

요구사항:
- 반드시 한국어로 작성
- 서론/사족 없이 마크다운 본문만 출력
- 첫 줄 H1은 `# 미팅노트`가 아니라 회의 내용을 대표하는 구체적 제목으로 작성. 예: `# AI DevOS 및 조직 구조 논의`
- 제목은 날짜/시간 없이 15~45자 정도로, DB나 파일 목록에서 구분 가능하게 핵심 주제 1~2개를 포함
- 없는 정보는 추측하지 말고 해당 섹션 생략
- 형식 A인 경우에만 화자(A/B)의 역할을 대화 맥락에서 추론해 표기 (예: "A(대표)", "B(컨설턴트)"). 확신이 없으면 A/B 그대로 사용. 형식 B는 화자 관련 표기 생략
- **고유명사 워싱 (웹검색 도구 활용)**: 회사명·인명·제품명·약어 중 전사 오류로 의심되는 항목은 사용 가능한 웹검색 도구(Claude Code WebSearch 또는 Codex web_search)로 검증 후 정정:
  1. 문맥(업종·규모·기능 등)에서 검색 쿼리를 설계해 실존 여부 확인
  2. 검증된 정정본으로 본문을 대체하고, 원문-정정본 쌍을 `## 검증 완료` 섹션에 기록
  3. 검색해도 확정 못한 항목만 `## 검증 필요` 섹션에 `원문 → 추정 (근거)` 형식으로 남김
  4. 불필요한 중복 검색은 피하고, 한 키워드당 최대 1회 검색
  예시 흐름: "나이스DI (기업정보 DB, 700만건)" → WebSearch: "한국 기업정보 DB 700만" → "NICE평가정보" 확인 → 본문 정정 + 검증 완료에 기록
- 확실한 숫자·날짜·금액은 원문 그대로 유지

구조:
# {회의 주제 제목}

## 요약
3~5줄 핵심 요약

## 주요 논의사항
- 주제별로 정리

## 결정사항
- 합의된 사항

## 액션 아이템
- [ ] 담당자(파악 가능 시) — 할 일

## 참석자/언급 인물
- 직원명단과 대화 맥락으로 확실히 특정되는 인물만 `이름(소속팀, 직책)` 형식으로 기록
- 동명이인/불확실한 호칭은 원문 유지

## 기타 메모
- 언급된 인물, 회사, 숫자, 링크 등 사실 정보

## 검증 완료
- `원문` → **정정** (출처·근거)

## 검증 필요
- 웹검색해도 확정 못한 전사 오류 의심 항목 (해당 시에만)
PROMPT
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
