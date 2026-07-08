#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
DRY_RUN=0

usage() {
  cat <<'USAGE'
Usage: build_employee_roster.sh [--dry-run]

Builds glossary/employee_roster.tsv from the Worxphere FamilyBab employee directory.
The output contains name, department, and position only; emails and phone numbers are not exported.
Names absent from FamilyBab but present in wdc's (worxphere-data-collectors) notion_users.json
identity export are appended with department only (no position, no email).
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
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

if [ -f "$BASE/.env" ]; then
  set -a; source "$BASE/.env"; set +a
fi

LOCAL_SOURCE="${EMPLOYEE_DIRECTORY_INDEX:-$HOME/project/worxphere-internal/packages/portal-to-notion/data/familybab/index.md}"
REMOTE_HOST="${EMPLOYEE_DIRECTORY_REMOTE_HOST:-macmini}"
REMOTE_SOURCE="${EMPLOYEE_DIRECTORY_REMOTE_INDEX:-/Users/agent/project/worxphere-internal/packages/portal-to-notion/data/familybab/index.md}"
WDC_USERS_SOURCE="${WDC_NOTION_USERS_PATH:-$HOME/project/worxphere-data-collectors/packages/notion-archive/archive/identity/notion_users.json}"
OUT="$BASE/glossary/employee_roster.tsv"
TMP_SOURCE=""

cleanup() {
  if [ -n "$TMP_SOURCE" ]; then
    rm -f "$TMP_SOURCE"
  fi
  return 0
}
trap cleanup EXIT

if [ -f "$LOCAL_SOURCE" ]; then
  SOURCE="$LOCAL_SOURCE"
elif ssh -o ConnectTimeout=5 -o BatchMode=yes "$REMOTE_HOST" "test -f '$REMOTE_SOURCE'" 2>/dev/null; then
  TMP_SOURCE="$(mktemp)"
  scp -q "$REMOTE_HOST:$REMOTE_SOURCE" "$TMP_SOURCE"
  SOURCE="$TMP_SOURCE"
else
  echo "employee directory source not found; skipped roster refresh" >&2
  exit 0
fi

args=(--source "$SOURCE" --out "$OUT")
if [ -f "$WDC_USERS_SOURCE" ]; then
  args+=(--wdc-users "$WDC_USERS_SOURCE")
fi
if [ "$DRY_RUN" -eq 1 ]; then
  args+=(--dry-run)
fi

python3 "$BASE/sh/build_employee_roster.py" "${args[@]}"
