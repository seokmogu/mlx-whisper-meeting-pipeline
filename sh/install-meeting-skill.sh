#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
SKILL_SOURCE="${MEETING_NOTES_SKILL_DIR:-$BASE/skills/meeting-minutes}"
SKILL_NAME="${MEETING_NOTES_SKILL_NAME:-worxphere-meeting-minutes}"
TARGET="$CODEX_HOME/skills/$SKILL_NAME"

if [ ! -f "$SKILL_SOURCE/SKILL.md" ]; then
  echo "meeting notes skill source not found: $SKILL_SOURCE/SKILL.md" >&2
  exit 1
fi

mkdir -p "$CODEX_HOME/skills"
rm -rf "$TARGET"
cp -R "$SKILL_SOURCE" "$TARGET"

echo "Installed meeting notes Codex skill to $TARGET"
