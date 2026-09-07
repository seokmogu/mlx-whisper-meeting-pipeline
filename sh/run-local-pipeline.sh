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
  6. propose and deterministically validate lexical transcript corrections
  7. generate Markdown meeting notes
  8. render date-partitioned human-review Markdown and optional SVG
  9. generate meeting context review artifacts
 10. retain incomplete jobs for retry and enqueue only completed notes

Options:
  --dry-run   Report what would run without copying, transcribing, writing notes, or generating reviews.
  --force-notes
              Regenerate existing meeting notes with backup, then regenerate matching reviews.
  --only TARGET
              Restrict note/review work to NAME, PROJECT/NAME, NAME.md, or PROJECT/NAME.md.
              An existing target retries its preview/review without regenerating the note.
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

  # FD 9 remains open in this shell, retaining the helper's flock. Never unlink
  # the inode: a contender must lock the same file, even after a stale PID.
  exec 9>>"$LOCK"
  if python3 - "$$" <<'PY'
import fcntl
import os
import sys

try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(75)
os.ftruncate(9, 0)
os.write(9, (sys.argv[1] + "\n").encode())
PY
  then
    trap 'exec 9>&-' EXIT
  else
    lock_status=$?
    exec 9>&-
    if [ "$lock_status" -eq 75 ]; then
      echo "another local pipeline instance holds the lock, exit"
      exit 0
    fi
    echo "cannot acquire local pipeline lock" >&2
    exit "$lock_status"
  fi
else
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') local pipeline dry-run ==="
fi

if [ -f "$BASE/.env" ]; then
  set -a; source "$BASE/.env"; set +a
fi

read -r -a PROJECTS <<<"${MEETING_PROJECTS:-worxphere}"
REVIEWER_DIR="${MEETING_CONTEXT_REVIEWER_DIR:-$(cd "$BASE/.." && pwd)/meeting-context-reviewer}"
REVIEW_PROFILE="${MEETING_REVIEW_PROFILE:-profiles/ax-os}"

sync_output="$(mktemp)"
manual_output="$(mktemp)"
prepare_output="$(mktemp)"
if [ "$DRY_RUN" -eq 0 ]; then
  trap 'rm -f "$sync_output" "$manual_output" "$prepare_output"; exec 9>&-' EXIT
else
  trap 'rm -f "$sync_output" "$manual_output" "$prepare_output"' EXIT
fi

completion_command=(python3 "$BASE/sh/complete_local_notes.py"
  --base "$BASE" --reviewer "$REVIEWER_DIR" --profile "$REVIEW_PROFILE")
[ "$FORCE_NOTES" -eq 1 ] && completion_command+=(--force-notes)
[ -n "$ONLY" ] && completion_command+=(--only "$ONLY")

refresh_meeting_identity_context() {
  local mode="${1:-write}"
  if [ "$mode" = "dry-run" ]; then
    "$BASE/sh/build_employee_roster.sh" --dry-run || echo "dry-run: employee roster refresh skipped"
    if [ -d "$BASE/notes" ]; then
      echo "dry-run: glossary extraction would refresh changed outputs under $BASE/glossary"
      echo "dry-run: identity ledger would refresh changed output $BASE/glossary/identity_ledger.md"
    fi
    return 0
  fi

  "$BASE/sh/build_employee_roster.sh" || echo "employee roster refresh skipped"
  if [ -d "$BASE/notes" ]; then
    "$BASE/sh/extract_glossary.py" "$BASE/notes" "$BASE/glossary" || echo "glossary extraction skipped"
    "$BASE/sh/build_identity_ledger.py" "$BASE/notes" "$BASE/glossary/identity_ledger.md" || echo "identity ledger refresh skipped"
  fi
}

if [ "$DRY_RUN" -eq 1 ]; then
  refresh_meeting_identity_context dry-run
  "$BASE/sh/sync-voice-memos.sh" --dry-run | tee "$sync_output"
  "$BASE/sh/import-manual-audio.sh" --dry-run | tee "$manual_output"
  "$BASE/sh/prepare-audio-queue.py" --dry-run | tee "$prepare_output"
else
  refresh_meeting_identity_context
  "$BASE/sh/sync-voice-memos.sh"
  "$BASE/sh/import-manual-audio.sh"
  "$BASE/sh/prepare-audio-queue.py"
fi

unprocessed=0
for proj in "${PROJECTS[@]}"; do
  for audio in "$BASE/audio/$proj"/*.m4a; do
    [ -e "$audio" ] || continue
    name="$(basename "$audio" .m4a)"
    [ -f "$BASE/transcripts/$proj/$name.txt" ] || unprocessed=$((unprocessed + 1))
  done
done
pending_jobs="$("${completion_command[@]}" --has-work)"

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
  "${completion_command[@]}" --dry-run
  if [ "${MEETING_TRANSCRIPT_CORRECTION:-1}" != "0" ]; then
    correction_dry_args=(--dry-run)
    [ "$FORCE_NOTES" -eq 1 ] && correction_dry_args+=(--force)
    [ -n "$ONLY" ] && correction_dry_args+=(--only "$ONLY")
    if [ "${#correction_dry_args[@]}" -gt 0 ]; then
      "$BASE/sh/correct-transcripts.sh" "${correction_dry_args[@]}"
    else
      "$BASE/sh/correct-transcripts.sh"
    fi
  fi
  echo "dry-run: date-partitioned Notion-readable previews would be generated for completed notes"
  echo "dry-run: meeting context reviews would be generated for new or incomplete jobs"
  echo "dry-run: completed notes would be registered for a separate Notion publication decision"
  echo "dry-run: no audio copied, transcripts written, notes generated, or reviews generated"
  exit 0
fi

if [ "$unprocessed" -eq 0 ] && [ "$pending_jobs" -eq 0 ]; then
  echo "no unprocessed audio or pending note/review jobs, exit"
  exit 0
fi

echo "$unprocessed file(s) unprocessed, running local transcription and note generation"
if [ "$unprocessed" -gt 0 ]; then
  "$BASE/sh/transcribe.sh"
  "$BASE/sh/filter-low-content-transcripts.py"
fi

if [ "${MEETING_TRANSCRIPT_CORRECTION:-1}" != "0" ]; then
  correction_args=()
  [ "$FORCE_NOTES" -eq 1 ] && correction_args+=(--force)
  [ -n "$ONLY" ] && correction_args+=(--only "$ONLY")
  if [ "${#correction_args[@]}" -gt 0 ]; then
    correction_command=("$BASE/sh/correct-transcripts.sh" "${correction_args[@]}")
  else
    correction_command=("$BASE/sh/correct-transcripts.sh")
  fi
  if ! "${correction_command[@]}"; then
    echo "transcript correction stage failed; continue with verified artifact or raw transcript" >&2
  fi
fi

echo "completing durable note/review jobs..."
completion_status=0
"${completion_command[@]}" || completion_status=$?
refresh_meeting_identity_context
if [ "$completion_status" -ne 0 ]; then
  echo "local pipeline incomplete; pending jobs retained for retry" >&2
  exit "$completion_status"
fi

echo "=== local pipeline done ==="
