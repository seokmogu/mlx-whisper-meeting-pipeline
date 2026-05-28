#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LOCK="$BASE/logs/pipeline.lock"
LOG="$BASE/logs/pipeline.log"
UPLOAD_PENDING="$BASE/state/notion-upload/pending.txt"

mkdir -p "$BASE/logs"

exec >> "$LOG" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') pipeline triggered ==="

if [ -e "$LOCK" ]; then
  pid="$(cat "$LOCK" 2>/dev/null || echo "")"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "another instance running (pid=$pid), exit"
    exit 0
  fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

"$BASE/sh/sync-voice-memos.sh"

if [ -f "$BASE/.env" ]; then
  set -a; source "$BASE/.env"; set +a
fi
read -r -a PROJECTS <<<"${MEETING_PROJECTS:-worxphere}"

shopt -s nullglob

unprocessed=0
total_audio=0
for proj in "${PROJECTS[@]}"; do
  for a in "$BASE/audio/$proj"/*.m4a; do
    [ -e "$a" ] || continue
    total_audio=$((total_audio + 1))
    name="$(basename "$a" .m4a)"
    [ -f "$BASE/notes/$proj/$name.md" ] || unprocessed=$((unprocessed + 1))
  done
done

if [ "$total_audio" -eq 0 ]; then
  if [ -n "${NOTION_UPLOAD_DATABASE_ID:-}" ] && [ -s "$UPLOAD_PENDING" ]; then
    echo "no audio files, retrying pending Notion uploads..."
    "$BASE/sh/upload-notion-notes.sh" --pending-file "$UPLOAD_PENDING" || \
      echo "notion upload failed (pending queue retained): $UPLOAD_PENDING"
  fi
  echo "no audio files in any MEETING_PROJECTS subdir, skip remote run"
  exit 0
fi

if [ "$unprocessed" -eq 0 ]; then
  if [ -n "${NOTION_UPLOAD_DATABASE_ID:-}" ] && [ -s "$UPLOAD_PENDING" ]; then
    echo "all audio already processed, retrying pending Notion uploads..."
    "$BASE/sh/upload-notion-notes.sh" --pending-file "$UPLOAD_PENDING" || \
      echo "notion upload failed (pending queue retained): $UPLOAD_PENDING"
  fi
  echo "all audio already processed, skip remote run"
  exit 0
fi

echo "$unprocessed file(s) unprocessed, running remote pipeline..."
"$BASE/sh/run-remote.sh"
echo "=== done ==="
