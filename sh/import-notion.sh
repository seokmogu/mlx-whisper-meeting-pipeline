#!/bin/bash
# DEPRECATED: superseded by import-notion-api.sh (stable public Notion API).
# This path scrapes the Mac Notion app's local SQLite cache and is kept only as a
# fallback for environments without API access. Prefer import-notion-api.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
REMOTE_BASE="project/meeting-notes"
REMOTE_DB="\$HOME/Library/Application Support/Notion/notion.db"

if [ -f "$BASE/.env" ]; then set -a; source "$BASE/.env"; set +a; fi
: "${REMOTE_HOST:=compute-host}"
: "${NOTION_TARGET_PROJECT:=worxphere}"
# Notion에서 온 전사는 .env의 NOTION_TARGET_PROJECT 서브디렉터리로 분류.
TRANSCRIPTS_DIR="$BASE/transcripts/$NOTION_TARGET_PROJECT"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

SINCE="${1:-}"

echo "[1/4] snapshotting notion.db on $REMOTE_HOST..."
ssh "$REMOTE_HOST" "sqlite3 \"$REMOTE_DB\" \".backup '/tmp/notion_snapshot.db'\""

echo "[2/4] fetching snapshot..."
rsync -az "$REMOTE_HOST:/tmp/notion_snapshot.db" "$TMP_DIR/notion.db"
ssh "$REMOTE_HOST" "rm -f /tmp/notion_snapshot.db"

echo "[3/4] extracting transcripts..."
mkdir -p "$TRANSCRIPTS_DIR"
args=("$TMP_DIR/notion.db" "$TRANSCRIPTS_DIR")
[ -n "$SINCE" ] && args+=(--since "$SINCE")
python3 "$BASE/sh/import_notion_db.py" "${args[@]}"

echo "[4/4] pushing new transcripts to $REMOTE_HOST (so make-notes.sh can see them)..."
rsync -av --ignore-existing \
  "$TRANSCRIPTS_DIR/" \
  "$REMOTE_HOST:$REMOTE_BASE/transcripts/$NOTION_TARGET_PROJECT/"

echo "---"
echo "done. latest transcripts:"
ls -lt "$TRANSCRIPTS_DIR" | head -10
