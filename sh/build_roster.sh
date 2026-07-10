#!/bin/bash
# SECONDARY: the active pipeline uses build_employee_roster.sh (FamilyBab + wdc,
# -> glossary/employee_roster.tsv). This dumps active workspace members from
# Notion's local cache into glossary/roster.tsv, which make-notes.sh consults only
# as a fallback when employee_roster.tsv is absent. Uses NOTION_SPACE_ID and
# ROSTER_EMAIL_DOMAIN from .env; run manually/periodically if you need the fallback.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
GLOSSARY_DIR="$BASE/glossary"

if [ -f "$BASE/.env" ]; then set -a; source "$BASE/.env"; set +a; fi

: "${REMOTE_HOST:=compute-host}"
: "${NOTION_SPACE_ID:?NOTION_SPACE_ID not set in .env}"
: "${ROSTER_EMAIL_DOMAIN:?ROSTER_EMAIL_DOMAIN not set in .env}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "[1/3] snapshotting notion.db on $REMOTE_HOST..."
ssh "$REMOTE_HOST" "sqlite3 \"\$HOME/Library/Application Support/Notion/notion.db\" \".backup '/tmp/notion_snapshot.db'\""
rsync -az "$REMOTE_HOST:/tmp/notion_snapshot.db" "$TMP_DIR/notion.db"
ssh "$REMOTE_HOST" "rm -f /tmp/notion_snapshot.db"

mkdir -p "$GLOSSARY_DIR"
OUT="$GLOSSARY_DIR/roster.tsv"

echo "[2/3] extracting workspace members..."
sqlite3 -separator $'\t' "$TMP_DIR/notion.db" <<SQL > "$OUT"
SELECT nu.name, nu.email
FROM space_user su
JOIN notion_user nu ON nu.id = su.user_id
WHERE su.space_id = '$NOTION_SPACE_ID'
  AND nu.name IS NOT NULL AND nu.name != ''
  AND nu.email LIKE '%@${ROSTER_EMAIL_DOMAIN}'
  AND nu.email NOT LIKE 'notion-automation@%'
  AND nu.name NOT LIKE '[Bot]%'
  AND (nu.is_banned IS NULL OR nu.is_banned = 0)
  AND nu.deleted_time IS NULL
ORDER BY nu.name;
SQL

count=$(wc -l < "$OUT" | tr -d ' ')
echo "[3/3] pushing roster to $REMOTE_HOST..."
rsync -av "$OUT" "$REMOTE_HOST:project/meeting-notes/glossary/roster.tsv"

echo "---"
echo "wrote $count members to $OUT"
head -5 "$OUT"
echo "..."
