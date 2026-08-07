#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
TARGET=""
ATTENDEES=""

usage() {
  cat <<'USAGE'
Usage: set-meeting-attendees.sh --meeting PROJECT/NAME --attendees "이름1, 이름2"

Stores user-confirmed attendees for one meeting. make-notes.sh treats this
metadata as stronger evidence than previous-meeting context when mapping speakers.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --meeting)
      TARGET="${2:?--meeting requires PROJECT/NAME}"
      shift
      ;;
    --attendees)
      ATTENDEES="${2:?--attendees requires a comma-separated name list}"
      shift
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

TARGET="${TARGET%.txt}"
TARGET="${TARGET%.md}"
case "$TARGET" in
  */*) ;;
  *)
    echo "--meeting must use PROJECT/NAME" >&2
    exit 2
    ;;
esac

if [ -z "$ATTENDEES" ]; then
  echo "--attendees must not be empty" >&2
  exit 2
fi
case "$ATTENDEES" in
  *$'\n'*|*$'\r'*)
    echo "--attendees must be a single line" >&2
    exit 2
    ;;
esac

project="${TARGET%%/*}"
name="${TARGET#*/}"
case "$project/$name" in
  *'..'*|*'//'*)
    echo "invalid meeting target: $TARGET" >&2
    exit 2
    ;;
esac

out_dir="${MEETING_ATTENDEES_DIR:-$BASE/state/meeting-attendees}/$project"
out="$out_dir/$name.txt"
mkdir -p "$out_dir"
printf '%s\n' "$ATTENDEES" > "$out"
echo "meeting attendees saved: $project/$name -> $ATTENDEES"
