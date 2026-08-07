#!/bin/bash
set -euo pipefail

# Voice Memos → project subdirectory routing.
#
# If VOICE_MEMO_FORCE_PROJECT is set, every recording from this Mac is routed to
# that project regardless of the Voice Memos title. Otherwise routing falls back
# to title prefixes from VOICE_MEMO_ROUTING, e.g.:
#   VOICE_MEMO_ROUTING="worxphere:worxphere 웍스피어:worxphere"
# Anything that doesn't match a rule lands in audio/unsorted/ unless
# VOICE_MEMO_DEFAULT_PROJECT is set.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SRC="$HOME/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
DB="$SRC/CloudRecordings.db"
DST_BASE="$BASE/audio"
DRY_RUN=0
MARK_EXISTING=0
TMP_FILES=()

cleanup() {
  [ "${#TMP_FILES[@]}" -eq 0 ] || rm -f "${TMP_FILES[@]}"
}
trap cleanup EXIT

usage() {
  cat <<'USAGE'
Usage: sync-voice-memos.sh [--dry-run] [--mark-existing]

Copies completed macOS Voice Memos recordings into project audio folders.

Options:
  --dry-run        Report copy/promote decisions without writing files.
  --mark-existing  Record current Voice Memos as already seen, without copying.
  -h, --help       Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --mark-existing)
      MARK_EXISTING=1
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

# .env is sourced if available; this script may also be launched directly.
if [ -f "$BASE/.env" ]; then
  set -a; source "$BASE/.env"; set +a
fi

read -r -a PROJECTS <<<"${MEETING_PROJECTS:-worxphere}"
read -r -a ROUTING_RULES <<<"${VOICE_MEMO_ROUTING:-}"
VOICE_MEMO_MIN_AGE_SECONDS="${VOICE_MEMO_MIN_AGE_SECONDS:-60}"
VOICE_MEMO_FORCE_PROJECT="${VOICE_MEMO_FORCE_PROJECT:-}"
VOICE_MEMO_DEFAULT_PROJECT="${VOICE_MEMO_DEFAULT_PROJECT:-}"
VOICE_MEMO_USE_SEEN_STATE="${VOICE_MEMO_USE_SEEN_STATE:-0}"
VOICE_MEMO_SEEN_FILE="${VOICE_MEMO_SEEN_FILE:-$BASE/state/voice-memos-seen.txt}"
VOICE_MEMO_TITLE_DIR="${VOICE_MEMO_TITLE_DIR:-$BASE/state/voice-memo-titles}"

if [ "$DRY_RUN" -eq 0 ]; then
  mkdir -p "$DST_BASE/unsorted"
  for p in "${PROJECTS[@]}"; do mkdir -p "$DST_BASE/$p"; done
fi

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

is_project() {
  local candidate="$1"
  for project in "${PROJECTS[@]}"; do
    [ "$candidate" = "$project" ] && return 0
  done
  return 1
}

classify() {
  local label="$1"
  local label_fold
  if [ -n "$VOICE_MEMO_FORCE_PROJECT" ] && is_project "$VOICE_MEMO_FORCE_PROJECT"; then
    echo "$VOICE_MEMO_FORCE_PROJECT"
    return
  fi
  label_fold="$(printf '%s' "$label" | tr '[:upper:]' '[:lower:]')"
  for rule in "${ROUTING_RULES[@]}"; do
    local prefix="${rule%%:*}"
    local project="${rule#*:}"
    local prefix_fold
    [ -z "$prefix" ] && continue
    is_project "$project" || continue
    prefix_fold="$(printf '%s' "$prefix" | tr '[:upper:]' '[:lower:]')"
    case "$label_fold" in
      "$prefix_fold"*) echo "$project"; return ;;
    esac
  done
  if [ -n "$VOICE_MEMO_DEFAULT_PROJECT" ] && is_project "$VOICE_MEMO_DEFAULT_PROJECT"; then
    echo "$VOICE_MEMO_DEFAULT_PROJECT"
    return
  fi
  echo "unsorted"
}

exists_in_project() {
  # Files already routed into a project subdir are not retried.
  # `unsorted/` is intentionally excluded: when a Voice Memos label is renamed
  # later, the next sync cycle should be able to promote (unsorted → project).
  local name="$1"
  for sub in "${PROJECTS[@]}"; do
    [ -e "$DST_BASE/$sub/$name" ] && return 0
  done
  return 1
}

filesystem_name() {
  local name="$1"
  printf '%s' "$name" | sed -E 's/[[:space:]]+/_/g; s/_+/_/g; s/^_//; s/_$//'
}

write_voice_memo_title() {
  local project="$1"
  local target_name="$2"
  local title="$3"
  local stem="${target_name%.m4a}"
  [ "$DRY_RUN" -eq 0 ] || return 0
  [ "$project" != "unsorted" ] || return 0
  [ -n "$title" ] || return 0
  mkdir -p "$VOICE_MEMO_TITLE_DIR/$project"
  printf '%s\n' "$title" > "$VOICE_MEMO_TITLE_DIR/$project/$stem.txt"
}

file_mtime_epoch() {
  local file="$1"
  stat -f %m "$file" 2>/dev/null || stat -c %Y "$file" 2>/dev/null
}

new_temp_file() {
  local tmp
  tmp="$(mktemp)"
  TMP_FILES+=("$tmp" "$tmp.err")
  echo "$tmp"
}

write_recording_list() {
  local out="$1"
  if ! find "$SRC" -maxdepth 1 -type f -name '*.m4a' -print0 > "$out" 2>"$out.err"; then
    cat "$out.err" >&2
    echo "Voice Memos folder cannot be listed. Grant Full Disk Access to /bin/bash or the launchd runner, then retry." >&2
    exit 1
  fi
  rm -f "$out.err"
}

seen_enabled() {
  [ "$VOICE_MEMO_USE_SEEN_STATE" = "1" ] || [ "$VOICE_MEMO_USE_SEEN_STATE" = "true" ] || [ "$VOICE_MEMO_USE_SEEN_STATE" = "yes" ]
}

seen_contains() {
  local name="$1"
  [ -f "$VOICE_MEMO_SEEN_FILE" ] && grep -Fxq "$name" "$VOICE_MEMO_SEEN_FILE"
}

mark_seen() {
  local name="$1"
  [ "$DRY_RUN" -eq 1 ] && return
  mkdir -p "$(dirname "$VOICE_MEMO_SEEN_FILE")"
  seen_contains "$name" || printf '%s\n' "$name" >> "$VOICE_MEMO_SEEN_FILE"
}

if [ "$MARK_EXISTING" -eq 1 ]; then
  recording_list="$(new_temp_file)"
  write_recording_list "$recording_list"
  if [ "$DRY_RUN" -eq 1 ]; then
    count="$(tr -cd '\0' < "$recording_list" | wc -c | tr -d ' ')"
    echo "dry-run mark-existing: $count current Voice Memos would be recorded in $VOICE_MEMO_SEEN_FILE"
    exit 0
  fi
  mkdir -p "$(dirname "$VOICE_MEMO_SEEN_FILE")"
  tmp_seen="$(mktemp)"
  TMP_FILES+=("$tmp_seen")
  {
    [ -f "$VOICE_MEMO_SEEN_FILE" ] && cat "$VOICE_MEMO_SEEN_FILE"
    while IFS= read -r -d '' file; do
      basename "$file"
    done < "$recording_list"
  } | sort -u > "$tmp_seen"
  mv "$tmp_seen" "$VOICE_MEMO_SEEN_FILE"
  count="$(wc -l < "$VOICE_MEMO_SEEN_FILE" | tr -d ' ')"
  echo "marked existing Voice Memos as seen: $count file(s) -> $VOICE_MEMO_SEEN_FILE"
  exit 0
fi

copied=0
skipped=0
too_new=0
promoted=0
unsorted=0
routed=0
recording_list="$(new_temp_file)"
write_recording_list "$recording_list"

while IFS= read -r -d '' file; do
  name="$(basename "$file")"
  target_name="$(filesystem_name "$name")"
  label="$(lookup_label "$name")"
  sub="$(classify "$label")"
  write_voice_memo_title "$sub" "$target_name" "$label"
  if seen_enabled && seen_contains "$name"; then
    skipped=$((skipped + 1))
    continue
  fi
  now="$(date +%s)"
  mtime="$(file_mtime_epoch "$file" || echo "$now")"
  age=$((now - mtime))
  if [ "$age" -lt "$VOICE_MEMO_MIN_AGE_SECONDS" ]; then
    echo "skip too-new recording: $name (age=${age}s, min=${VOICE_MEMO_MIN_AGE_SECONDS}s)"
    too_new=$((too_new + 1))
    continue
  fi
  if exists_in_project "$target_name"; then
    skipped=$((skipped + 1))
    seen_enabled && mark_seen "$name" || true
    continue
  fi
  if [ "$sub" = "unsorted" ]; then
    if [ ! -e "$DST_BASE/unsorted/$target_name" ]; then
      if [ "$DRY_RUN" -eq 1 ]; then
        echo "dry-run copy: unsorted/$target_name  (source: $name, label: ${label:-<none>})"
      else
        cp -p "$file" "$DST_BASE/unsorted/$target_name"
        echo "copied: unsorted/$target_name  (source: $name, label: ${label:-<none>})"
      fi
      copied=$((copied + 1))
      unsorted=$((unsorted + 1))
    fi
    # Already in unsorted: leave it; we'll re-check the label next cycle.
  elif [ -e "$DST_BASE/unsorted/$target_name" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "dry-run promote: unsorted/$target_name → $sub/$target_name  (source: $name, label: $label)"
    else
      mv "$DST_BASE/unsorted/$target_name" "$DST_BASE/$sub/$target_name"
      echo "promoted: unsorted/$target_name → $sub/$target_name  (source: $name, label: $label)"
    fi
    promoted=$((promoted + 1))
    routed=$((routed + 1))
    seen_enabled && mark_seen "$name" || true
  else
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "dry-run copy: $sub/$target_name  (source: $name, label: $label)"
    else
      cp -p "$file" "$DST_BASE/$sub/$target_name"
      echo "copied: $sub/$target_name  (source: $name, label: $label)"
    fi
    copied=$((copied + 1))
    routed=$((routed + 1))
    seen_enabled && mark_seen "$name" || true
  fi
done < "$recording_list"

echo "---"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "dry-run: no files copied, moved, or promoted"
fi
echo "copied: $copied (unsorted: $unsorted), promoted: $promoted, routed: $routed, skipped (already exists/seen): $skipped, too-new: $too_new"
if [ "$unsorted" -gt 0 ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "!! $unsorted file(s) would land in audio/unsorted/ — rename the Voice Memo title to match a VOICE_MEMO_ROUTING prefix, or move the file manually." >&2
  else
    echo "!! $unsorted file(s) landed in audio/unsorted/ — rename the Voice Memo title to match a VOICE_MEMO_ROUTING prefix, or move the file manually." >&2
  fi
fi
