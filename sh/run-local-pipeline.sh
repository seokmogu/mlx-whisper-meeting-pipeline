#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LOCK="$BASE/logs/local-pipeline.lock"
LOG="$BASE/logs/local-pipeline.log"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
DRY_RUN=0
FORCE_NOTES=0
ONLY=""

usage() {
  cat <<'USAGE'
Usage: run-local-pipeline.sh [--dry-run] [--force-notes] [--only PROJECT/NAME]

Runs the local Voice Memos pipeline:
  1. sync completed Voice Memos into audio/<project>/
  2. import manually copied phone recordings from manual-audio/<project>/
  3. merge adjacent restart segments and quarantine obvious silence
  4. transcribe new audio
  5. quarantine low-content/noise transcripts
  6. generate Markdown meeting notes
  7. generate meeting context review artifacts

Options:
  --dry-run   Report what would run without copying, transcribing, writing notes, or generating reviews.
  --force-notes
              Regenerate existing meeting notes with backup, then regenerate matching reviews.
  --only TARGET
              Restrict note/review regeneration to NAME, PROJECT/NAME, NAME.md, or PROJECT/NAME.md.
  -h, --help  Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --force-notes)
      FORCE_NOTES=1
      ;;
    --only)
      ONLY="${2:?--only requires a target}"
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

if [ "$DRY_RUN" -eq 0 ]; then
  mkdir -p "$BASE/logs"
  exec >> "$LOG" 2>&1
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') local pipeline triggered ==="

  if [ -e "$LOCK" ]; then
    pid="$(cat "$LOCK" 2>/dev/null || echo "")"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      echo "another local pipeline instance running (pid=$pid), exit"
      exit 0
    fi
  fi
  echo $$ > "$LOCK"
  trap 'rm -f "$LOCK"' EXIT
else
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') local pipeline dry-run ==="
fi

if [ -f "$BASE/.env" ]; then
  set -a; source "$BASE/.env"; set +a
fi

read -r -a PROJECTS <<<"${MEETING_PROJECTS:-worxphere}"
REVIEWER_DIR="${MEETING_CONTEXT_REVIEWER_DIR:-$(cd "$BASE/.." && pwd)/meeting-context-reviewer}"
REVIEW_PROFILE="${MEETING_REVIEW_PROFILE:-profiles/ax-os}"

before_notes="$(mktemp)"
after_notes="$(mktemp)"
new_notes="$(mktemp)"
sync_output="$(mktemp)"
manual_output="$(mktemp)"
prepare_output="$(mktemp)"
review_targets_temp=""
if [ "$DRY_RUN" -eq 0 ]; then
  trap 'rm -f "$LOCK" "$before_notes" "$after_notes" "$new_notes" "$sync_output" "$manual_output" "$prepare_output" "$review_targets_temp"' EXIT
else
  trap 'rm -f "$before_notes" "$after_notes" "$new_notes" "$sync_output" "$manual_output" "$prepare_output" "$review_targets_temp"' EXIT
fi

list_notes() {
  for proj in "${PROJECTS[@]}"; do
    find "$BASE/notes/$proj" -maxdepth 1 -type f -name '*.md' 2>/dev/null || true
  done | sort
}

normalize_target() {
  local target="$1"
  target="${target%.txt}"
  target="${target%.md}"
  echo "$target"
}

matches_only() {
  local proj="$1"
  local name="$2"
  [ -z "$ONLY" ] && return 0
  local target
  target="$(normalize_target "$ONLY")"
  [ "$target" = "$name" ] || [ "$target" = "$proj/$name" ]
}

list_matching_notes() {
  for proj in "${PROJECTS[@]}"; do
    for note in "$BASE/notes/$proj"/*.md; do
      [ -e "$note" ] || continue
      name="$(basename "$note" .md)"
      if matches_only "$proj" "$name"; then
        echo "$note"
      fi
    done
  done | sort
}

list_notes > "$before_notes"

if [ "$DRY_RUN" -eq 1 ]; then
  "$BASE/sh/build_employee_roster.sh" --dry-run || echo "dry-run: employee roster refresh skipped"
  "$BASE/sh/sync-voice-memos.sh" --dry-run | tee "$sync_output"
  "$BASE/sh/import-manual-audio.sh" --dry-run | tee "$manual_output"
  "$BASE/sh/prepare-audio-queue.py" --dry-run | tee "$prepare_output"
else
  "$BASE/sh/build_employee_roster.sh" || echo "employee roster refresh skipped"
  "$BASE/sh/sync-voice-memos.sh"
  "$BASE/sh/import-manual-audio.sh"
  "$BASE/sh/prepare-audio-queue.py"
fi

unprocessed=0
matching_transcripts=0
for proj in "${PROJECTS[@]}"; do
  for audio in "$BASE/audio/$proj"/*.m4a; do
    [ -e "$audio" ] || continue
    name="$(basename "$audio" .m4a)"
    [ -f "$BASE/notes/$proj/$name.md" ] || unprocessed=$((unprocessed + 1))
  done
  for transcript in "$BASE/transcripts/$proj"/*.txt; do
    [ -e "$transcript" ] || continue
    name="$(basename "$transcript" .txt)"
    if matches_only "$proj" "$name"; then
      matching_transcripts=$((matching_transcripts + 1))
    fi
  done
done

if [ "$unprocessed" -eq 0 ] && { [ "$FORCE_NOTES" -eq 0 ] || [ "$matching_transcripts" -eq 0 ]; }; then
  echo "no unprocessed audio, exit"
  exit 0
fi

if [ "$DRY_RUN" -eq 1 ]; then
  routed_from_sync="$(awk -F'routed: ' '/routed: / {split($2, a, ","); value=a[1]} END {print value + 0}' "$sync_output")"
  imported_manual="$(awk -F'manual imported: ' '/manual imported: / {split($2, a, ","); value=a[1]} END {print value + 0}' "$manual_output")"
  merged_groups="$(awk -F'merged_groups=' '/audio prepared: / {split($2, a, ","); value=a[1]} END {print value + 0}' "$prepare_output")"
  trimmed_audio="$(awk -F'trimmed=' '/audio prepared: / {split($2, a, ","); value=a[1]} END {print value + 0}' "$prepare_output")"
  rejected_audio="$(awk -F'rejected=' '/audio prepared: / {value=$2} END {print value + 0}' "$prepare_output")"
  echo "dry-run: $unprocessed existing audio file(s) would be transcribed and converted into notes"
  if [ "$routed_from_sync" -gt 0 ]; then
    echo "dry-run: $routed_from_sync newly synced Voice Memo file(s) would also enter the project audio queue"
  fi
  if [ "$imported_manual" -gt 0 ]; then
    echo "dry-run: $imported_manual manually copied phone recording(s) would also enter the project audio queue"
  fi
  if [ "$merged_groups" -gt 0 ]; then
    echo "dry-run: $merged_groups adjacent restart group(s) would be merged before transcription"
  fi
  if [ "$trimmed_audio" -gt 0 ]; then
    echo "dry-run: $trimmed_audio audio file(s) would have leading/trailing non-speech trimmed"
  fi
  if [ "$rejected_audio" -gt 0 ]; then
    echo "dry-run: $rejected_audio obvious silence/too-short audio file(s) would be quarantined"
  fi
  if [ "$FORCE_NOTES" -eq 1 ]; then
    echo "dry-run: $matching_transcripts transcript file(s) would be regenerated into notes with backup"
  fi
  echo "dry-run: meeting context reviews would be generated for newly created notes"
  echo "dry-run: no audio copied, transcripts written, notes generated, or reviews generated"
  exit 0
fi

echo "$unprocessed file(s) unprocessed, running local transcription and note generation"
if [ "$unprocessed" -gt 0 ]; then
  "$BASE/sh/transcribe.sh"
  "$BASE/sh/filter-low-content-transcripts.py"
fi

make_notes_args=()
if [ "$FORCE_NOTES" -eq 1 ]; then
  make_notes_args+=(--force)
fi
if [ -n "$ONLY" ]; then
  make_notes_args+=(--only "$ONLY")
fi
if [ "$FORCE_NOTES" -eq 1 ] || [ -n "$ONLY" ]; then
  "$BASE/sh/make-notes.sh" "${make_notes_args[@]}"
else
  "$BASE/sh/make-notes.sh"
fi

list_notes > "$after_notes"
comm -13 "$before_notes" "$after_notes" > "$new_notes"

review_targets="$new_notes"
if [ "$FORCE_NOTES" -eq 1 ]; then
  review_targets_temp="$(mktemp)"
  list_matching_notes > "$review_targets_temp"
  review_targets="$review_targets_temp"
fi

if [ ! -s "$review_targets" ]; then
  echo "no new notes generated"
  exit 0
fi

if [ ! -d "$REVIEWER_DIR" ]; then
  echo "meeting-context-reviewer not found: $REVIEWER_DIR"
  exit 0
fi

echo "generating meeting context reviews..."
while IFS= read -r note; do
  [ -n "$note" ] || continue
  stem="$(basename "$note" .md | tr ' ' '-')"
  out_dir="$REVIEWER_DIR/reviews/$stem"
  (
    cd "$REVIEWER_DIR"
    review_args=(
      --profile "$REVIEW_PROFILE"
      --meeting "$note"
      --out "$out_dir"
    )
    if [ -s "$BASE/glossary/employee_roster.tsv" ]; then
      review_args+=(--employee-roster "$BASE/glossary/employee_roster.tsv")
    fi
    uv run meeting-context-reviewer review "${review_args[@]}"
  )
done < "$review_targets"

echo "=== local pipeline done ==="
