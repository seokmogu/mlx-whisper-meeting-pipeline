#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

if [ -f "$BASE/.env" ]; then
  set -a
  source "$BASE/.env"
  set +a
fi

database_id_arg=""
expect_database_id=0
for arg in "$@"; do
  if [ "$expect_database_id" -eq 1 ]; then
    database_id_arg="$arg"
    expect_database_id=0
    continue
  fi
  case "$arg" in
    --database-id)
      expect_database_id=1
      ;;
    --database-id=*)
      database_id_arg="${arg#--database-id=}"
      ;;
  esac
done

if [ -z "${NOTION_UPLOAD_DATABASE_ID:-}" ] && [ -z "$database_id_arg" ]; then
  echo "NOTION_UPLOAD_DATABASE_ID not set, skip Notion upload"
  exit 0
fi

TOOLKIT_DIR="${NOTION_NATIVE_TOOLKIT_DIR:-$HOME/project/notion-native-toolkit}"
PYTHON="$TOOLKIT_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "notion-native-toolkit venv python not found: $PYTHON" >&2
  exit 1
fi

export PYTHONPATH="$TOOLKIT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

exec "$PYTHON" "$BASE/sh/upload_notion_notes.py" "$@"
