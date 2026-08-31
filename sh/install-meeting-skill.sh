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
STAGING="$(mktemp -d "$CODEX_HOME/skills/.${SKILL_NAME}.install.XXXXXX")"
cp -R "$SKILL_SOURCE"/. "$STAGING"

BACKUP=""
if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
  BACKUP_DIR="$CODEX_HOME/skill-backups/$SKILL_NAME"
  mkdir -p "$BACKUP_DIR"
  BACKUP="$BACKUP_DIR/$(date '+%Y%m%d-%H%M%S')"
  mv "$TARGET" "$BACKUP"
  echo "Backed up previous skill to $BACKUP"
fi

if ! mv "$STAGING" "$TARGET"; then
  if [ -n "$BACKUP" ] && [ ! -e "$TARGET" ]; then
    mv "$BACKUP" "$TARGET"
  fi
  echo "failed to install meeting notes skill" >&2
  exit 1
fi

echo "Installed meeting notes Codex skill to $TARGET"
