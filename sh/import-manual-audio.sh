#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
DRY_RUN=0

usage() {
  cat <<'USAGE'
Usage: import-manual-audio.sh [--dry-run]

Imports manually copied phone recordings into audio/<project>/.

Drop files into:
  manual-audio/<project>/*.m4a

Options:
  --dry-run   Report imports without moving files.
  -h, --help  Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
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

if [ -f "$BASE/.env" ]; then
  set -a; source "$BASE/.env"; set +a
fi

read -r -a PROJECTS <<<"${MEETING_PROJECTS:-worxphere}"
MANUAL_AUDIO_DIR="${MANUAL_AUDIO_DIR:-$BASE/manual-audio}"
MANUAL_AUDIO_MIN_AGE_SECONDS="${MANUAL_AUDIO_MIN_AGE_SECONDS:-60}"

file_mtime_epoch() {
  local file="$1"
  stat -f %m "$file" 2>/dev/null || stat -c %Y "$file" 2>/dev/null
}

unique_target() {
  local target="$1"
  if [ ! -e "$target" ]; then
    echo "$target"
    return
  fi
  local dir base stem ext candidate i
  dir="$(dirname "$target")"
  base="$(basename "$target")"
  ext="${base##*.}"
  stem="${base%.*}"
  i=1
  while [ "$i" -le 999 ]; do
    candidate="$dir/$stem-import-$i.$ext"
    if [ ! -e "$candidate" ]; then
      echo "$candidate"
      return
    fi
    i=$((i + 1))
  done
  echo "cannot find unique target for $target" >&2
  exit 1
}

filesystem_name() {
  local name="$1"
  printf '%s' "$name" | sed -E 's/[[:space:]]+/_/g; s/_+/_/g; s/^_//; s/_$//'
}

imported=0
too_new=0
missing_dirs=0

if [ "$DRY_RUN" -eq 0 ]; then
  mkdir -p "$MANUAL_AUDIO_DIR"
fi

for project in "${PROJECTS[@]}"; do
  inbox="$MANUAL_AUDIO_DIR/$project"
  out_dir="$BASE/audio/$project"
  if [ "$DRY_RUN" -eq 0 ]; then
    mkdir -p "$inbox" "$out_dir"
  elif [ ! -d "$inbox" ]; then
    missing_dirs=$((missing_dirs + 1))
    continue
  fi

  while IFS= read -r -d '' file; do
    name="$(basename "$file")"
    target_name="$(filesystem_name "$name")"
    now="$(date +%s)"
    mtime="$(file_mtime_epoch "$file" || echo "$now")"
    age=$((now - mtime))
    if [ "$age" -lt "$MANUAL_AUDIO_MIN_AGE_SECONDS" ]; then
      echo "skip too-new manual audio: $project/$name (age=${age}s, min=${MANUAL_AUDIO_MIN_AGE_SECONDS}s)"
      too_new=$((too_new + 1))
      continue
    fi

    target="$(unique_target "$out_dir/$target_name")"
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "dry-run import manual audio: $project/$name -> ${target#$BASE/}"
    else
      mv "$file" "$target"
      echo "imported manual audio: $project/$name -> ${target#$BASE/}"
    fi
    imported=$((imported + 1))
  done < <(find "$inbox" -maxdepth 1 -type f -iname '*.m4a' -print0 2>/dev/null)
done

echo "---"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "dry-run: no manual audio moved"
fi
echo "manual imported: $imported, too-new: $too_new, missing inbox dirs: $missing_dirs"
