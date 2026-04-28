#!/bin/bash
# Fetch Notion AI meeting transcripts via the public Notion API.
# Replaces import-notion.sh (notion.db SQLite scraping) with the stable API path.
# Runs from the local (Air) host. Writes to transcripts/ and pushes to the remote.
set -euo pipefail

BASE="${MEETING_BASE_DIR:-$HOME/project/meeting-notes}"
REMOTE_BASE="project/meeting-notes"

set -a
source "$BASE/.env"
set +a
: "${REMOTE_HOST:=compute-host}"
: "${NOTION_TOKEN:?NOTION_TOKEN not set in .env}"
: "${NOTION_MEETING_DBS:?NOTION_MEETING_DBS not set in .env (comma-separated DB IDs)}"
: "${NOTION_TARGET_PROJECT:=projectA}"
# Notion에서 온 전사는 .env의 NOTION_TARGET_PROJECT 서브디렉터리로 분류.
TRANSCRIPTS_DIR="$BASE/transcripts/$NOTION_TARGET_PROJECT"

SINCE="${1:-}"

echo "[1/2] fetching transcripts via Notion API..."
db_args=()
IFS=',' read -ra DB_IDS <<< "$NOTION_MEETING_DBS"
for db in "${DB_IDS[@]}"; do
  db_args+=(--db "$(echo "$db" | tr -d ' ')")
done
since_args=()
if [ -n "$SINCE" ]; then
  since_args=(--since "$SINCE")
fi

mkdir -p "$TRANSCRIPTS_DIR"
python3 "$BASE/sh/import_notion_api.py" "$TRANSCRIPTS_DIR" "${db_args[@]}" ${since_args[@]+"${since_args[@]}"}

echo "[2/2] pushing new transcripts to $REMOTE_HOST..."
rsync -av --ignore-existing \
  "$TRANSCRIPTS_DIR/" \
  "$REMOTE_HOST:$REMOTE_BASE/transcripts/$NOTION_TARGET_PROJECT/"

echo "---"
echo "done. latest transcripts:"
ls -lt "$TRANSCRIPTS_DIR" | head -10
