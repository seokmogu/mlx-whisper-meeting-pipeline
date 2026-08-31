#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
BATCH_DATE="$(date '+%Y-%m-%d')"
OUTPUT_BASE=""
ONLY=""
LIMIT=0
DRY_RUN=0
EXCLUDE_REGEX="${MEETING_REBUILD_EXCLUDE_REGEX:-링크온|linkon}"
REVIEWER_DIR="${MEETING_CONTEXT_REVIEWER_DIR:-$(cd "$BASE/.." && pwd)/meeting-context-reviewer}"
REVIEW_PROFILE="${MEETING_REVIEW_PROFILE:-profiles/ax-os}"

usage() {
  cat <<'USAGE'
Usage: rebuild-notes-to-candidate.sh [--batch-date YYYY-MM-DD] [--output-base DIR] [--only PROJECT/NAME] [--limit N] [--dry-run]

Rebuilds meeting notes into a separate, resumable candidate tree. Canonical
notes, existing readable files, publication queues, and Notion are untouched.

Outputs:
  meeting-note-rebuilds/active/<batch-date>/notes/<project>/<stem>.md
  meeting-note-rebuilds/active/<batch-date>/notion-readable/<project>/YYYY-MM-DD/<stem>.md
  meeting-note-rebuilds/active/<batch-date>/reviews/<project>/<stem>/

Options:
  --batch-date DATE  Candidate batch folder date (default: today).
  --output-base DIR  Exact candidate batch root; overrides --batch-date.
  --only TARGET      Rebuild only NAME or PROJECT/NAME.
  --limit N          Process at most N unfinished targets; 0 means all.
  --dry-run          List selected targets and paths without writing or calling an LLM.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --batch-date)
      BATCH_DATE="${2:?--batch-date requires YYYY-MM-DD}"
      shift
      ;;
    --output-base)
      OUTPUT_BASE="${2:?--output-base requires a directory}"
      shift
      ;;
    --only)
      ONLY="${2:?--only requires a target}"
      shift
      ;;
    --limit)
      LIMIT="${2:?--limit requires a value}"
      shift
      ;;
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

if ! [[ "$BATCH_DATE" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "--batch-date must be YYYY-MM-DD" >&2
  exit 2
fi
if ! [[ "$LIMIT" =~ ^[0-9]+$ ]]; then
  echo "--limit must be a non-negative integer" >&2
  exit 2
fi

if [ -z "$OUTPUT_BASE" ]; then
  OUTPUT_BASE="$BASE/meeting-note-rebuilds/active/$BATCH_DATE"
elif [[ "$OUTPUT_BASE" != /* ]]; then
  OUTPUT_BASE="$BASE/$OUTPUT_BASE"
fi

normalize_target() {
  local value="$1"
  value="${value%.txt}"
  value="${value%.md}"
  printf '%s' "$value"
}

matches_only() {
  local project="$1"
  local stem="$2"
  [ -z "$ONLY" ] && return 0
  local wanted
  wanted="$(normalize_target "$ONLY")"
  [ "$wanted" = "$stem" ] || [ "$wanted" = "$project/$stem" ]
}

targets_file="$(mktemp)"
trap 'rm -f "$targets_file"' EXIT

for project_dir in "$BASE/transcripts"/*; do
  [ -d "$project_dir" ] || continue
  project="$(basename "$project_dir")"
  for transcript in "$project_dir"/*.txt; do
    [ -e "$transcript" ] || continue
    stem="$(basename "$transcript" .txt)"
    matches_only "$project" "$stem" || continue
    [ -f "$BASE/audio/$project/$stem.m4a" ] || continue
    source_label="$stem"
    title_file="$BASE/state/voice-memo-titles/$project/$stem.txt"
    if [ -s "$title_file" ]; then
      source_label="$source_label $(tr '\n' ' ' < "$title_file")"
    fi
    if [ -n "$EXCLUDE_REGEX" ] && printf '%s' "$source_label" | grep -Eiq "$EXCLUDE_REGEX"; then
      continue
    fi
    printf '%s\t%s\n' "$project" "$stem" >> "$targets_file"
  done
done

sort -o "$targets_file" "$targets_file"
selected=0
completed=0
skipped=0

while IFS=$'\t' read -r project stem; do
  [ -n "$project" ] || continue
  candidate="$OUTPUT_BASE/notes/$project/$stem.md"
  if [ -s "$candidate" ]; then
    skipped=$((skipped + 1))
    continue
  fi
  if [ "$LIMIT" -gt 0 ] && [ "$selected" -ge "$LIMIT" ]; then
    break
  fi
  selected=$((selected + 1))
  echo "=== CANDIDATE NOTE $selected $project/$stem ==="
  echo "candidate: $candidate"
  if [ "$DRY_RUN" -eq 1 ]; then
    continue
  fi

  "$BASE/sh/make-notes.sh" \
    --output-root "$OUTPUT_BASE/notes" \
    --only "$project/$stem"
  python3 "$BASE/sh/validate_meeting_note.py" "$candidate"
  python3 "$BASE/sh/notion_publication.py" render \
    --base "$OUTPUT_BASE" \
    "$candidate"

  if [ -d "$REVIEWER_DIR" ]; then
    review_out="$OUTPUT_BASE/reviews/$project/$stem"
    (
      cd "$REVIEWER_DIR"
      review_args=(
        --profile "$REVIEW_PROFILE"
        --meeting "$candidate"
        --out "$review_out"
      )
      if [ -s "$BASE/glossary/employee_roster.tsv" ]; then
        review_args+=(--employee-roster "$BASE/glossary/employee_roster.tsv")
      fi
      uv run meeting-context-reviewer review "${review_args[@]}"
    )
    [ -s "$review_out/review.md" ]
  fi
  python3 "$BASE/sh/audit_meeting_candidate.py" \
    --batch-root "$OUTPUT_BASE" \
    --source-base "$BASE"
  completed=$((completed + 1))
done < "$targets_file"

echo "---"
echo "candidate batch: $OUTPUT_BASE"
echo "selected: $selected, completed: $completed, skipped existing: $skipped"
echo "Notion publication: not requested"
