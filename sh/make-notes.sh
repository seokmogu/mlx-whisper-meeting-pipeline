#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
TRANSCRIPT_DIR="$BASE/transcripts"
NOTES_DIR="$BASE/notes"
CORRECTED_TRANSCRIPT_DIR="${MEETING_CORRECTED_TRANSCRIPT_DIR:-$BASE/state/corrected-transcripts}"
TRANSCRIPT_CORRECTION_DIR="${MEETING_TRANSCRIPT_CORRECTION_DIR:-$BASE/state/transcript-corrections}"
MEETING_NOTES_SKILL="${MEETING_NOTES_SKILL:-$BASE/skills/meeting-minutes/SKILL.md}"
MEETING_PREVIOUS_NOTES_LIMIT="${MEETING_PREVIOUS_NOTES_LIMIT:-3}"
MEETING_PREVIOUS_NOTE_MAX_LINES="${MEETING_PREVIOUS_NOTE_MAX_LINES:-160}"
MEETING_ATTENDEES_DIR="${MEETING_ATTENDEES_DIR:-$BASE/state/meeting-attendees}"
VOICE_MEMO_TITLE_DIR="${VOICE_MEMO_TITLE_DIR:-$BASE/state/voice-memo-titles}"
FORCE=0
DRY_RUN=0
ONLY=""

usage() {
  cat <<'USAGE'
Usage: make-notes.sh [--force] [--only PROJECT/NAME] [--dry-run]

Generates Markdown meeting notes from transcripts.

Options:
  --force          Regenerate existing notes. Existing note is backed up first.
  --only TARGET    Process only NAME, PROJECT/NAME, NAME.txt, or PROJECT/NAME.txt.
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
  find "$out_dir" -maxdepth 1 -type f -name '*.md' ! -name "$current_name.md" -print \
    | sort \
    | awk -v current="$current_name.md" '{ name = $0; sub(/^.*\//, "", name); if (name < current) print }' \
    | tail -n "$limit" > "$tmp"
  if [ ! -s "$tmp" ]; then
    rm -f "$tmp"
    return 0
  fi

  cat <<PREV

---
이전 회의록 참고자료:
- 같은 project($proj)의 최근 회의록에서 후속 액션/결정/리스크 판단에 필요한 섹션만 발췌했다.
- Previous Action Follow-up·반복 이슈·중복 액션 판단에만 사용한다.
- 이전 회의의 참석자나 화자 매핑을 현재 회의에 그대로 이어 붙이지 않는다. 현재 회의의 확정 참석자 메타데이터와 현재 녹취의 직접 호칭·3인칭 언급을 우선한다.

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

emit_current_meeting_identity_context() {
  local proj="$1"
  local name="$2"
  local attendees_file="$MEETING_ATTENDEES_DIR/$proj/$name.txt"
  local voice_title_file="$VOICE_MEMO_TITLE_DIR/$proj/$name.txt"

  if [ ! -s "$attendees_file" ] && [ ! -s "$voice_title_file" ]; then
    return 0
  fi

  cat <<'IDENTITY'

---
**현재 회의 참석자 메타데이터 (현재 회의 화자 판정의 최우선 근거)**
- `사용자 확정 참석자`가 있으면 현재 회의 참석자로 확정한다. 최근 회의록의 참석자·화자 매핑이나 주제 연속성이 이를 덮어쓰면 안 된다.
- 현재 녹취에서 확정 참석자가 다른 인물을 3인칭으로 언급하면, 그 언급 인물을 현재 화자로 바꾸지 않는다.
- 확정 참석자와 발음이 비슷하다는 이유만으로 모든 호칭을 참석자에게 합치지 않는다. `X님이`, `X님한테`, `X님 조직`처럼 문법적으로 3인칭인 호칭은 별도 언급 인물로 유지하고, 정확한 실명이 불명확하면 `확인 필요`로 남긴다.
- 누적 표기 사전보다 현재 녹취의 직접 호칭/3인칭 문법이 우선한다. 표기 사전은 현재 회의 참석자나 화자를 결정하는 근거가 아니다.
- Voice Memo 제목은 보조 힌트다. `참석자: 이름1, 이름2`, `이름1, 이름2 미팅`처럼 이름 목록이 명시되고 직원 디렉토리와 일치할 때만 참석자 근거로 사용한다.
- 날짜·시간·장소·자동 생성 제목은 참석자 근거가 아니다.

IDENTITY
  if [ -s "$attendees_file" ]; then
    printf '%s' '- 사용자 확정 참석자: '
    tr '\n' ' ' < "$attendees_file" | sed -E 's/[[:space:]]+$//'
    printf '\n'
  fi
  if [ -s "$voice_title_file" ]; then
    printf '%s' '- Voice Memo 제목: '
    tr '\n' ' ' < "$voice_title_file" | sed -E 's/[[:space:]]+$//'
    printf '\n'
  fi
}

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
    note_transcript="$transcript"
    correction_manifest="$TRANSCRIPT_CORRECTION_DIR/$proj/$name.json"
    corrected_transcript="$CORRECTED_TRANSCRIPT_DIR/$proj/$name.txt"
    if [ -s "$corrected_transcript" ] && [ -s "$correction_manifest" ]; then
      if "$BASE/sh/apply_transcript_corrections.py" verify \
          --raw "$transcript" \
          --corrected "$corrected_transcript" \
          --manifest "$correction_manifest" \
          --quiet; then
        note_transcript="$corrected_transcript"
      else
        echo "stale/invalid corrected transcript ignored: $proj/$name" >&2
        correction_manifest=""
      fi
    else
      correction_manifest=""
    fi

    if [ -f "$out" ] && [ "$FORCE" -eq 0 ]; then
      skipped=$((skipped + 1))
      continue
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
      if [ "$note_transcript" != "$transcript" ]; then
        echo "dry-run transcript source: verified corrected derivative ($proj/$name)"
      else
        echo "dry-run transcript source: raw ($proj/$name)"
      fi
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
- stdout이 그대로 Output note 파일에 저장된다. 파일 저장/작성 완료 보고를 하지 말고 회의록 본문만 출력
- 첫 줄은 반드시 `# {specific meeting title}` 형식의 H1이어야 함
- `회의록을 작성했습니다`, `저장했습니다`, `확인 부탁`, `조정하겠습니다` 같은 대화형 보고 문장 금지
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
      echo "- Original source transcript: \`$transcript\`"
      echo "- Transcript used for note generation: \`$note_transcript\`"
      echo "- Output note: \`$out\`"
      echo "- Generated label: \`AI 추정\`"
      if [ -n "$correction_manifest" ]; then
        cat <<'CORRECTION'

---
**검증된 제한 교정 transcript 사용**
- 원본 transcript는 변경되지 않았고, 아래 accepted lexical patch만 적용된 파생본이 현재 입력이다.
- accepted 교정은 결정·담당·기한을 새로 만드는 근거가 아니며, 이름·제품명·조직명·약어 표기 정규화에만 사용한다.
- accepted 교정의 원문 → 정정은 `## 11. 검증 완료`에 기록한다.

CORRECTION
        "$BASE/sh/apply_transcript_corrections.py" show-accepted --manifest "$correction_manifest"
      fi
      emit_previous_note_context "$proj" "$name" "$out_dir"
      if [ -s "$BASE/glossary/employee_roster.tsv" ]; then
        cat <<'ROSTER'

---
**이름 정규화 — 직원 디렉토리**
형식: `이름<TAB>소속팀<TAB>직책<TAB>employment_status<TAB>마지막 재직 확인일<TAB>출처`.
기존 3열 행은 상태 미확인으로 취급한다. 전사의 "~님" 호칭, 짧은 이름, 유사 발음을 이 명부와 매칭해 인물을 특정한다.
- 이름이 명부에서 1명으로 확정되고 문맥이 맞을 때만 `이름(소속팀, 직책)`으로 정규화
- 동명이인이거나 소속/역할 문맥이 맞지 않으면 원문을 유지하고 `## 검증 필요`에 남김
- `former`는 과거 회의·언급 인물 식별에만 사용하고 현재 참석자, 현재 소속, 액션 담당자라는 근거로 사용하지 않음
- `unverified`는 이름 후보로만 사용하고 현재 재직 여부를 단정하지 않음
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
      if [ -s "$BASE/glossary/glossary_prompt.txt" ] || [ -s "$BASE/glossary/glossary_hotwords.txt" ]; then
        cat <<'GLOSSARY'

---
**용어/고유명사 보정 참고**
- 아래 용어는 과거 회의록에서 추출한 제품명, 조직명, 프로젝트명, 인명 후보이다.
- 전사 원문과 문맥이 맞고 과거 회의록/직원명단과 충돌하지 않을 때만 보정한다.
- 애매하면 `검증 필요`에 남기고, 확정 보정은 `## 검증 완료`에 기록한다.

GLOSSARY
        if [ -s "$BASE/glossary/glossary_prompt.txt" ]; then
          echo "용어 프롬프트:"
          cat "$BASE/glossary/glossary_prompt.txt"
        fi
        if [ -s "$BASE/glossary/glossary_hotwords.txt" ]; then
          echo "핫워드:"
          cat "$BASE/glossary/glossary_hotwords.txt"
        fi
      fi
      if [ -s "$BASE/glossary/identity_ledger.md" ]; then
        cat <<'LEDGER'

---
**누적 확정 표기 사전 (최우선 참고)**
- 아래는 과거 회의록의 `## 검증 완료`에서 축적한, 이미 확정된 STT 오인식 → 정정 매핑이다.
- 확정 횟수가 높을수록 신뢰도가 높다. 전사 원문에 같은 변형이 나오고 문맥이 맞으면 이 정정을 우선 적용하고, 매번 처음부터 다시 추정하지 않는다.
- 누적 사전으로 확정 가능한 이름/용어는 본문, 표, 액션아이템, 참석자/언급 인물 섹션에서 정정 표기를 사용한다. 원문 변형은 `## 11. 검증 완료`의 정정 근거로만 남긴다.
- 누적 사전과 직원 디렉토리가 충돌하면, 회의 문맥이 누적 사전의 정정 대상과 맞는지 먼저 판단한다. 예: AI Product/거버넌스 문맥의 `성모`는 `구석모` 정정 후보로 본다.
- 단, 화자 라벨(A/B)과 실제 인물 매칭은 이 회의 전사 문맥으로 재확인한다. 표기 사전은 "이 변형은 이 사람/용어를 뜻한다"는 사전일 뿐, 특정 화자가 누구인지까지 결정하지 않는다.
- 확정 보정은 `## 검증 완료`에 다시 기록해 사전이 계속 누적되게 한다.

LEDGER
        cat "$BASE/glossary/identity_ledger.md"
      fi
      if [ "${MEETING_PHONETIC_CANDIDATES:-1}" != "0" ] && [ -s "$BASE/glossary/employee_roster.tsv" ]; then
        phon_cand="$(mktemp)"
        if "$BASE/sh/phonetic_name_candidates.py" "$note_transcript" "$BASE/glossary/employee_roster.tsv" "$phon_cand" 2>/dev/null && [ -s "$phon_cand" ]; then
          cat <<'PHON'

---
**발음 유사 인물 후보 (자모 대조 — 사전에 아직 없는 이름 보강)**
누적 사전에 없는 새 STT 변형이라도, 발음이 가까운 사내 직원 후보를 아래에 제시한다.
회의 문맥(팀·역할·이전 회의)과 맞을 때만 정정하고, 동명이인이거나 애매하면 `검증 필요`에 남긴다.

PHON
          cat "$phon_cand"
        fi
        rm -f "$phon_cand"
      fi
      if [ "${WDC_MEETING_CONTEXT:-1}" != "0" ]; then
        wdc_context="$BASE/state/wdc-context/$proj/$name.md"
        if "$BASE/sh/build_wdc_meeting_context.py" "$note_transcript" "$wdc_context" --glossary-dir "$BASE/glossary"; then
          if [ -s "$wdc_context" ]; then
            cat <<'WDC_CONTEXT'

---
**WDC 전사 근거 컨텍스트**
아래는 Worxphere Data Collectors(WDC)가 수집한 Slack/Notion/GitLab evidence index에서 생성한 짧은 snippet/metadata 참고자료이다.
- transcript에 없는 결정, 담당자, 기한을 WDC만으로 만들지 않는다.
- 고유명사/조직명/프로젝트명 보정과 기존 업무 연속성 판단에만 사용한다.
- 충돌하거나 애매하면 `검증 필요`에 남긴다.

WDC_CONTEXT
            cat "$wdc_context"
          fi
        else
          echo "WDC meeting context generation failed; continue without WDC context." >&2
        fi
      fi
      emit_current_meeting_identity_context "$proj" "$name"
      cat <<'TAIL'

---
녹취록:
TAIL
      cat "$note_transcript"
    } | "$BASE/sh/run-note-llm.sh" \
        --out "$out"

    made=$((made + 1))
  done
done

echo "---"
echo "made: $made, overwritten: $overwritten, skipped (already exists): $skipped"
projects_csv="$(IFS=,; echo "${PROJECTS[*]}")"
echo "destination: $NOTES_DIR/{$projects_csv}"
