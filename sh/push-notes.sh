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
  if [ -z "$(git status --porcelain)" ]; then
    echo "$proj: nothing to commit"
    continue
  fi

  new_count="$(git status --porcelain | wc -l | tr -d ' ')"
  git add -A
  git commit -q -m "Add $new_count note(s) — $(date '+%Y-%m-%d %H:%M')"
  git push -q origin main
  echo "$proj: pushed $new_count file(s)"
done
