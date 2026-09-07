#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LOCK="$BASE/logs/notion-publication.lock"
LOG="$BASE/logs/notion-publication.log"
MODE="prepare"
LIMIT=1
ONLY=""
VISIBILITY=""
APPROVAL_FILE=""

usage() {
  cat <<'USAGE'
Usage: run-notion-publication-pipeline.sh [--prepare|--preflight|--publish] [--limit N] [--only NOTE] [--visibility public|private] [--approval-file FILE]

Runs the separate publication-decision flow. The local meeting pipeline already
creates the date-partitioned readable preview. The default mode validates those
completed artifacts and prepares a request without touching Notion.

  --prepare    Validate reviewed artifacts and prepare a routing request (default)
  --preflight  Also fetch the routed Notion data source; no Notion write
  --publish    Publish one explicitly approved page through Codex Notion MCP
  --approval-file FILE
               Required only for --publish; binds one exact prepared request
  --limit N    Process newest pending notes first (default: 1)
  --only NOTE  Restrict processing to one queued note path
  --visibility Process only notes with explicit matching visibility metadata
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --prepare)
      MODE="prepare"
      ;;
    --preflight)
      MODE="preflight"
      ;;
    --publish)
      MODE="publish"
      ;;
    --limit)
      LIMIT="${2:?--limit requires a value}"
      shift
      ;;
    --only)
      ONLY="${2:?--only requires a note}"
      shift
      ;;
    --visibility)
      VISIBILITY="${2:?--visibility requires public or private}"
      shift
      ;;
    --approval-file)
      APPROVAL_FILE="${2:?--approval-file requires a file}"
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

if ! [[ "$LIMIT" =~ ^[1-9][0-9]*$ ]]; then
  echo "--limit must be a positive integer" >&2
  exit 2
fi
if [ -n "$VISIBILITY" ] && [ "$VISIBILITY" != "public" ] && [ "$VISIBILITY" != "private" ]; then
  echo "--visibility must be public or private" >&2
  exit 2
fi
if [ "$MODE" = "publish" ] && [ -z "$APPROVAL_FILE" ]; then
  echo "--publish requires --approval-file" >&2
  exit 2
fi
if [ "$MODE" = "publish" ] && [ "$LIMIT" -ne 1 ]; then
  echo "--publish requires --limit 1 and one exact approval file" >&2
  exit 2
fi

if [ -f "$BASE/.env" ]; then
  set -a
  source "$BASE/.env"
  set +a
fi

mkdir -p "$BASE/logs" "$BASE/state/notion-publication"
if [ "$MODE" = "publish" ]; then
  exec >>"$LOG" 2>&1
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') Notion publication triggered ==="
fi

if [ -e "$LOCK" ]; then
  pid="$(cat "$LOCK" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "another Notion publication instance is running (pid=$pid), exit"
    exit 0
  fi
fi
echo $$ >"$LOCK"
trap 'rm -f "$LOCK"' EXIT

args=(
  run
  --base "$BASE"
  --pending-file "$BASE/state/notion-publication/pending.txt"
  --state "$BASE/state/notion-publication/publications.json"
  --limit "$LIMIT"
)
if [ -n "$ONLY" ]; then
  args+=(--only "$ONLY")
fi
if [ -n "$VISIBILITY" ]; then
  args+=(--visibility "$VISIBILITY")
fi
if [ -n "$APPROVAL_FILE" ]; then
  args+=(--approval-file "$APPROVAL_FILE")
fi
case "$MODE" in
  preflight) args+=(--preflight) ;;
  publish) args+=(--publish) ;;
esac

python3 "$BASE/sh/notion_publication.py" reconcile \
  --base "$BASE" \
  --pending-file "$BASE/state/notion-publication/pending.txt" \
  --state "$BASE/state/notion-publication/publications.json"
python3 "$BASE/sh/notion_publication.py" "${args[@]}"
