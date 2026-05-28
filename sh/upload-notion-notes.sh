#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

if [ -f "$BASE/.env" ]; then
  set -a
  source "$BASE/.env"
  set +a
fi

if [ -z "${NOTION_UPLOAD_DATABASE_ID:-}" ]; then
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
