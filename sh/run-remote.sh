#!/bin/bash
set -euo pipefail

LOCAL_BASE="$HOME/project/meeting-notes"
REMOTE_BASE="project/meeting-notes"

set -a
source "$LOCAL_BASE/.env"
set +a

# 원격 호스트 선택. REMOTE_HOSTS(공백 분리 리스트)를 앞에서부터 ssh alive check 해
# 첫 번째로 응답하는 호스트를 쓴다. 구버전 .env의 REMOTE_HOST(단일 값)도 호환.
if [ -z "${REMOTE_HOSTS:-}" ]; then
  REMOTE_HOSTS="${REMOTE_HOST:-compute-host}"
fi

pick_remote_host() {
  for h in $REMOTE_HOSTS; do
    if ssh -o ConnectTimeout=5 -o BatchMode=yes "$h" 'true' 2>/dev/null; then
      printf '%s' "$h"
      return 0
    fi
  done
  return 1
}

if ! REMOTE_HOST="$(pick_remote_host)"; then
  echo "No reachable remote host in: $REMOTE_HOSTS" >&2
  exit 1
fi
echo "using REMOTE_HOST=$REMOTE_HOST (candidates: $REMOTE_HOSTS)"

# OAuth 토큰은 원격 호스트의 claude-oauth 프로파일에서 자체 조달한다 (make-notes.sh).
# 로컬 셸의 토큰을 ssh 너머로 넘기지 않는다 — launchd 등 비대화형 트리거 호환.

echo "[1/6] extracting glossary from past notes..."
python3 "$LOCAL_BASE/sh/extract_glossary.py" \
  "$LOCAL_BASE/notes" \
  "$LOCAL_BASE/glossary"

echo "[2/6] pushing audio, glossary, transcripts, notes to $REMOTE_HOST..."
rsync -av --exclude='.DS_Store' --exclude='unsorted/' \
  "$LOCAL_BASE/audio/" \
  "$REMOTE_HOST:$REMOTE_BASE/audio/"
rsync -av \
  "$LOCAL_BASE/glossary/" \
  "$REMOTE_HOST:$REMOTE_BASE/glossary/"
# 이미 처리된 transcript/note가 remote에 없으면 전사·노트 생성이 재시도되므로,
# 리모트 쪽 state도 로컬 기준으로 동기화해 skip 로직이 작동하게 한다.
rsync -av --exclude='.git/' --exclude='.DS_Store' \
  "$LOCAL_BASE/transcripts/" \
  "$REMOTE_HOST:$REMOTE_BASE/transcripts/"
rsync -av --exclude='.git/' --exclude='.DS_Store' \
  "$LOCAL_BASE/notes/" \
  "$REMOTE_HOST:$REMOTE_BASE/notes/"

echo "[3/6] running transcribe.sh on $REMOTE_HOST..."
ssh "$REMOTE_HOST" "bash $REMOTE_BASE/sh/transcribe.sh"

echo "[4/6] running make-notes.sh on $REMOTE_HOST..."
ssh "$REMOTE_HOST" "bash $REMOTE_BASE/sh/make-notes.sh"

echo "[5/6] pulling transcripts and notes back..."
rsync -av --exclude='.git/' \
  "$REMOTE_HOST:$REMOTE_BASE/transcripts/" \
  "$LOCAL_BASE/transcripts/"
rsync -av --exclude='.git/' \
  "$REMOTE_HOST:$REMOTE_BASE/notes/" \
  "$LOCAL_BASE/notes/"

echo "[6/6] committing and pushing notes to GitHub..."
"$LOCAL_BASE/sh/push-notes.sh" || echo "push-notes failed (non-fatal)"

# 원격 노드는 일회성 실행 노드로만 사용한다. 미팅 데이터는 로컬만 authoritative.
# pull-back이 끝난 이 시점에 원격의 audio/transcripts/notes/glossary는 더 이상 필요 없다.
echo "[cleanup] removing meeting data from $REMOTE_HOST (models cache는 유지)..."
ssh "$REMOTE_HOST" "rm -rf $REMOTE_BASE/audio $REMOTE_BASE/transcripts $REMOTE_BASE/notes $REMOTE_BASE/glossary" || echo "remote cleanup failed (non-fatal)"

read -r -a PROJECTS <<<"${MEETING_PROJECTS:-projectA projectB}"
echo "---"
echo "done. latest notes:"
for proj in "${PROJECTS[@]}"; do
  echo "-- $proj --"
  ls -lt "$LOCAL_BASE/notes/$proj/" 2>/dev/null | head -3
done
