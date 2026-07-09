#!/bin/bash
set -euo pipefail

# notes/<project> 각각이 독립 git 레포라고 가정 (사용자가 원격을 설정해 둔 경우).
# 새 노트가 있으면 커밋 후 원격으로 푸시. .env의 MEETING_PROJECTS를 따라 순회.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
NOTES_DIR="$BASE/notes"

if [ -f "$BASE/.env" ]; then
  set -a; source "$BASE/.env"; set +a
fi

read -r -a PROJECTS <<<"${MEETING_PROJECTS:-worxphere}"

for proj in "${PROJECTS[@]}"; do
  repo="$NOTES_DIR/$proj"
  [ -d "$repo/.git" ] || { echo "skip $proj: not a git repo"; continue; }

  cd "$repo"
  # Commit any new notes (skip the commit only when the tree is already clean).
  if [ -n "$(git status --porcelain)" ]; then
    new_count="$(git status --porcelain | wc -l | tr -d ' ')"
    git add -A
    git commit -q -m "Add $new_count note(s) — $(date '+%Y-%m-%d %H:%M')"
    echo "$proj: committed $new_count file(s)"
  fi

  # Always attempt a push so a commit stranded by a previous failed push is retried.
  # `if git push` keeps set -e from aborting the loop, so one project's failure
  # (network/auth/non-fast-forward) no longer skips the remaining projects.
  if git push -q origin main; then
    echo "$proj: pushed"
  else
    echo "$proj: push failed, will retry next run" >&2
  fi
done
