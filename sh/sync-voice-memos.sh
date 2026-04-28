#!/bin/bash
set -euo pipefail

# Voice Memos title (ZCUSTOMLABELFORSORTING) → project subdirectory routing.
# macOS Voice Memos keeps the title in CloudRecordings.db; the m4a file name is a
# raw timestamp, so we have to query the SQLite DB to get the user-set label.
#
# Routing is configured via VOICE_MEMO_ROUTING in .env, e.g.:
#   VOICE_MEMO_ROUTING="alpha:projectA beta:projectB"
# Means: titles starting with "alpha" → audio/projectA/, etc.
# Anything that doesn't match a rule lands in audio/unsorted/ for manual sorting.

BASE="${MEETING_BASE_DIR:-$HOME/project/meeting-notes}"
SRC="$HOME/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
DB="$SRC/CloudRecordings.db"
DST_BASE="$BASE/audio"

# .env is sourced if available; this script may also be launched directly.
if [ -f "$BASE/.env" ]; then
  set -a; source "$BASE/.env"; set +a
fi

read -r -a PROJECTS <<<"${MEETING_PROJECTS:-projectA projectB}"
read -r -a ROUTING_RULES <<<"${VOICE_MEMO_ROUTING:-}"

mkdir -p "$DST_BASE/unsorted"
for p in "${PROJECTS[@]}"; do mkdir -p "$DST_BASE/$p"; done

if [ ! -d "$SRC" ]; then
  echo "Voice Memos folder not found: $SRC" >&2
  exit 1
fi
if [ ! -f "$DB" ]; then
  echo "Voice Memos DB not found: $DB" >&2
  exit 1
fi

lookup_label() {
  local name="$1"
  local escaped="${name//\'/\'\'}"
  sqlite3 "$DB" "SELECT COALESCE(ZCUSTOMLABELFORSORTING, ZCUSTOMLABEL, '') FROM ZCLOUDRECORDING WHERE ZPATH='$escaped' LIMIT 1;" 2>/dev/null || echo ""
}

classify() {
  local label="$1"
  for rule in "${ROUTING_RULES[@]}"; do
    local prefix="${rule%%:*}"
    local project="${rule#*:}"
    [ -z "$prefix" ] && continue
    case "$label" in
      "$prefix"*) echo "$project"; return ;;
    esac
  done
  echo "unsorted"
}

exists_anywhere() {
  local name="$1"
  for sub in "${PROJECTS[@]}" unsorted; do
    [ -e "$DST_BASE/$sub/$name" ] && return 0
  done
  return 1
}

copied=0
skipped=0
unsorted=0

while IFS= read -r -d '' file; do
  name="$(basename "$file")"
  if exists_anywhere "$name"; then
    skipped=$((skipped + 1))
    continue
  fi
  label="$(lookup_label "$name")"
  sub="$(classify "$label")"
  cp -p "$file" "$DST_BASE/$sub/$name"
  echo "copied: $sub/$name  (label: ${label:-<none>})"
  copied=$((copied + 1))
  [ "$sub" = "unsorted" ] && unsorted=$((unsorted + 1))
done < <(find "$SRC" -maxdepth 1 -type f -name '*.m4a' -print0)

echo "---"
echo "copied: $copied (unsorted: $unsorted), skipped (already exists): $skipped"
if [ "$unsorted" -gt 0 ]; then
  echo "!! $unsorted file(s) landed in audio/unsorted/ — rename the Voice Memo title to match a VOICE_MEMO_ROUTING prefix, or move the file manually." >&2
fi
