#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
DRY_RUN=0

usage() {
  cat <<'USAGE'
Usage: build_employee_roster.sh [--dry-run]

Builds the history-preserving glossary/employee_roster.tsv used for meeting-name recognition.
A fresh FamilyBab snapshot confirms active employees. People missing from a fresh later snapshot
are retained as former employees. WDC Notion users widen coverage but never prove employment.
Emails, phone numbers, employee IDs, and internal identity keys are never exported to the glossary.
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
REMOTE_CONNECT_TIMEOUT_SECONDS="${EMPLOYEE_DIRECTORY_REMOTE_CONNECT_TIMEOUT_SECONDS:-5}"
LOCAL_COLLECTOR_PROJECT="${EMPLOYEE_DIRECTORY_LOCAL_PROJECT:-$HOME/project/worxphere-internal}"
LOCAL_COLLECTOR="${EMPLOYEE_DIRECTORY_LOCAL_COLLECTOR:-$LOCAL_COLLECTOR_PROJECT/packages/portal-to-notion/scripts/familybab_sync.sh}"
WDC_USERS_SOURCE="${WDC_NOTION_USERS_PATH:-$HOME/project/worxphere-data-collectors/packages/notion-archive/archive/identity/notion_users.json}"
SOURCE_MAX_AGE_HOURS="${EMPLOYEE_DIRECTORY_MAX_AGE_HOURS:-36}"
REMOTE_CHECK_INTERVAL_SECONDS="${EMPLOYEE_DIRECTORY_REMOTE_CHECK_INTERVAL_SECONDS:-3600}"
OUT="$BASE/glossary/employee_roster.tsv"
STATE_DIR="$BASE/state/employee-roster"
CACHE_SOURCE="$STATE_DIR/source-cache/familybab-index.md"
REMOTE_CHECK_STAMP="$STATE_DIR/remote-check.epoch"
HISTORY="$STATE_DIR/history.json"
STATUS_OUT="$STATE_DIR/status.json"
LOCAL_FALLBACK_LOG="$STATE_DIR/local-fallback.log"

file_mtime_epoch() {
  stat -f '%m' "$1" 2>/dev/null || stat -c '%Y' "$1" 2>/dev/null
}

SOURCE=""
SOURCE_ORIGIN="missing"
REMOTE_REFRESH_RESULT="not_needed"
LOCAL_FALLBACK_RESULT="not_needed"
if [ -f "$LOCAL_SOURCE" ]; then
  SOURCE="$LOCAL_SOURCE"
  SOURCE_ORIGIN="local_snapshot"
fi
if [ -f "$CACHE_SOURCE" ] && { [ -z "$SOURCE" ] || [ "$CACHE_SOURCE" -nt "$SOURCE" ]; }; then
  SOURCE="$CACHE_SOURCE"
  SOURCE_ORIGIN="macmini_cache"
fi

# The authoritative collector runs on the Mac mini. Probe it only when the best
# local snapshot is stale, and at most once per interval, so the 2-minute meeting
# watcher does not create an SSH storm when the Mac mini is unavailable.
if [ "$DRY_RUN" -eq 0 ]; then
  now="$(date +%s)"
  source_mtime=0
  if [ -n "$SOURCE" ]; then source_mtime="$(file_mtime_epoch "$SOURCE")"; fi
  max_age_seconds="$(python3 -c 'import sys; print(int(float(sys.argv[1]) * 3600))' "$SOURCE_MAX_AGE_HOURS")"
  last_check=0
  if [ -f "$REMOTE_CHECK_STAMP" ]; then read -r last_check < "$REMOTE_CHECK_STAMP" || last_check=0; fi
  if { [ -z "$SOURCE" ] || [ $((now - source_mtime)) -gt "$max_age_seconds" ]; } && [ $((now - last_check)) -ge "$REMOTE_CHECK_INTERVAL_SECONDS" ]; then
    mkdir -p "$STATE_DIR/source-cache"
    check_tmp="$(mktemp -d "$STATE_DIR/source-cache/.refresh.XXXXXX")"
    if scp -p -q -o ConnectTimeout="$REMOTE_CONNECT_TIMEOUT_SECONDS" -o BatchMode=yes "$REMOTE_HOST:$REMOTE_SOURCE" "$check_tmp/familybab-index.md" 2>/dev/null; then
      mv "$check_tmp/familybab-index.md" "$CACHE_SOURCE"
      REMOTE_REFRESH_RESULT="success"
      if [ -z "$SOURCE" ] || [ "$CACHE_SOURCE" -nt "$SOURCE" ]; then
        SOURCE="$CACHE_SOURCE"
        SOURCE_ORIGIN="macmini_cache"
      fi
      echo "employee directory source cache refreshed from $REMOTE_HOST" >&2
    else
      REMOTE_REFRESH_RESULT="failed"
      echo "employee directory remote refresh unavailable; retaining existing history" >&2
    fi
    rmdir "$check_tmp" 2>/dev/null || true

    # Re-evaluate all local candidates after the remote attempt. Another local
    # collector may have completed while scp was running.
    if [ -f "$LOCAL_SOURCE" ] && { [ -z "$SOURCE" ] || [ "$LOCAL_SOURCE" -nt "$SOURCE" ]; }; then
      SOURCE="$LOCAL_SOURCE"
      SOURCE_ORIGIN="local_snapshot"
    fi
    source_mtime=0
    if [ -n "$SOURCE" ]; then source_mtime="$(file_mtime_epoch "$SOURCE")"; fi

    # MacBook fallback is read-only against the portal and writes only local
    # collector snapshots. It never runs the Notion deploy mode.
    if [ -z "$SOURCE" ] || [ $((now - source_mtime)) -gt "$max_age_seconds" ]; then
      if [ -f "$LOCAL_COLLECTOR" ]; then
        echo "employee directory source still stale; collecting FamilyBab on MacBook" >&2
        if PROJECT="$LOCAL_COLLECTOR_PROJECT" bash "$LOCAL_COLLECTOR" collect >> "$LOCAL_FALLBACK_LOG" 2>&1; then
          local_mtime=0
          if [ -f "$LOCAL_SOURCE" ]; then local_mtime="$(file_mtime_epoch "$LOCAL_SOURCE")"; fi
          if [ "$local_mtime" -gt 0 ] && [ $((now - local_mtime)) -le "$max_age_seconds" ]; then
            SOURCE="$LOCAL_SOURCE"
            SOURCE_ORIGIN="macbook_fallback"
            LOCAL_FALLBACK_RESULT="success"
            echo "employee directory refreshed by MacBook fallback" >&2
          else
            LOCAL_FALLBACK_RESULT="stale_output"
            echo "MacBook fallback completed without a fresh FamilyBab snapshot" >&2
          fi
        else
          LOCAL_FALLBACK_RESULT="failed"
          echo "MacBook FamilyBab fallback failed; retaining existing history" >&2
        fi
      else
        LOCAL_FALLBACK_RESULT="not_configured"
        echo "MacBook FamilyBab fallback collector not found; retaining existing history" >&2
      fi
    fi
    printf '%s\n' "$now" > "$REMOTE_CHECK_STAMP"
  fi
fi

if [ -z "$SOURCE" ] || [ ! -f "$SOURCE" ]; then
  echo "employee directory source not found; skipped roster refresh" >&2
  exit 0
fi

source_mtime="$(file_mtime_epoch "$SOURCE")"
args=(
  --source "$SOURCE"
  --source-mtime-epoch "$source_mtime"
  --source-max-age-hours "$SOURCE_MAX_AGE_HOURS"
  --source-origin "$SOURCE_ORIGIN"
  --remote-refresh-result "$REMOTE_REFRESH_RESULT"
  --local-fallback-result "$LOCAL_FALLBACK_RESULT"
  --out "$OUT"
  --history "$HISTORY"
  --status-out "$STATUS_OUT"
)
HISTORICAL_SOURCE=""
if [ "$SOURCE" != "$LOCAL_SOURCE" ] && [ -f "$LOCAL_SOURCE" ] && [ "$LOCAL_SOURCE" -ot "$SOURCE" ]; then
  HISTORICAL_SOURCE="$LOCAL_SOURCE"
elif [ "$SOURCE" != "$CACHE_SOURCE" ] && [ -f "$CACHE_SOURCE" ] && [ "$CACHE_SOURCE" -ot "$SOURCE" ]; then
  HISTORICAL_SOURCE="$CACHE_SOURCE"
fi
if [ -n "$HISTORICAL_SOURCE" ]; then
  args+=(
    --historical-source "$HISTORICAL_SOURCE"
    --historical-source-mtime-epoch "$(file_mtime_epoch "$HISTORICAL_SOURCE")"
  )
fi
if [ -f "$WDC_USERS_SOURCE" ]; then
  args+=(--wdc-users "$WDC_USERS_SOURCE")
fi
if [ "$DRY_RUN" -eq 1 ]; then
  args+=(--dry-run)
fi

python3 "$BASE/sh/build_employee_roster.py" "${args[@]}"
