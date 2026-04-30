#!/bin/bash
set -euo pipefail

BASE="$HOME/project/meeting-notes"
TRANSCRIPT_DIR="$BASE/transcripts"
NOTES_DIR="$BASE/notes"

# Claude 호출은 이 호스트의 활성 프로파일(claude-oauth-run이 사용하는 것)에서 토큰을 조달한다.
# 이미 환경변수로 들어와 있으면 그것을 쓴다.
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  if command -v claude-oauth >/dev/null 2>&1; then
    export CLAUDE_CODE_OAUTH_TOKEN="$(claude-oauth print-token)"
  fi
fi
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "CLAUDE_CODE_OAUTH_TOKEN not set — 이 호스트의 claude-oauth 프로파일에서 토큰을 가져올 수 없습니다." >&2
  exit 1
fi

read -r -a PROJECTS <<<"${MEETING_PROJECTS:-projectA projectB}"

made=0
skipped=0

shopt -s nullglob
for proj in "${PROJECTS[@]}"; do
  in_dir="$TRANSCRIPT_DIR/$proj"
  out_dir="$NOTES_DIR/$proj"
  [ -d "$in_dir" ] || continue
  mkdir -p "$out_dir"

  for transcript in "$in_dir"/*.txt; do
    name="$(basename "$transcript" .txt)"
    out="$out_dir/$name.md"

    if [ -f "$out" ]; then
      skipped=$((skipped + 1))
      continue
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
- **고유명사 워싱 (WebSearch 활용)**: 회사명·인명·제품명·약어 중 전사 오류로 의심되는 항목은 반드시 WebSearch 도구로 검증 후 정정:
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

## 기타 메모
- 언급된 인물, 회사, 숫자, 링크 등 사실 정보

## 검증 완료
- `원문` → **정정** (출처·근거)

## 검증 필요
- 웹검색해도 확정 못한 전사 오류 의심 항목 (해당 시에만)
PROMPT
      if [ -s "$BASE/glossary/roster.tsv" ]; then
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
    } | env -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY -u CLAUDE_API_KEY \
        "$HOME/.local/bin/claude" -p \
        --tools "WebSearch" \
        --dangerously-skip-permissions \
        > "$out"

    made=$((made + 1))
  done
done

echo "---"
echo "made: $made, skipped (already exists): $skipped"
echo "destination: $NOTES_DIR/{${MEETING_PROJECTS// /,}}"
