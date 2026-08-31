#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LABEL="${NOTION_PUBLICATION_LAUNCHD_LABEL:-com.seokmogu.meeting-notion-publication}"
DOMAIN="gui/$(id -u)"
SERVICE="$DOMAIN/$LABEL"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
QUEUE_DIR="$BASE/state/notion-publication"
VOICE_TITLE_DIR="$BASE/state/voice-memo-titles"
DRY_RUN=0

usage() {
  cat <<'USAGE'
Usage: notion-publication-launchd.sh <install|uninstall|restart|status|kickstart> [--dry-run]

Manage the independent Notion publication worker. It watches only the
publication queue and runs after the audio/transcript/note/review flow has
finished and enqueued a note.
USAGE
}

command_name="${1:-}"
if [ -n "$command_name" ]; then
  shift
fi
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf 'dry-run:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

write_plist() {
  run mkdir -p "$(dirname "$TARGET")" "$BASE/logs" "$QUEUE_DIR" "$VOICE_TITLE_DIR"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry-run: write $TARGET"
    return
  fi
  temporary="$(mktemp)"
  cat >"$temporary" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$BASE/sh/run-notion-publication-pipeline.sh</string>
    <string>--publish</string>
    <string>--limit</string>
    <string>1</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>MEETING_BASE_DIR</key>
    <string>$BASE</string>
  </dict>
  <key>WatchPaths</key>
  <array>
    <string>$QUEUE_DIR</string>
    <string>$VOICE_TITLE_DIR</string>
  </array>
  <key>StartInterval</key>
  <integer>300</integer>
  <key>RunAtLoad</key>
  <false/>
  <key>ThrottleInterval</key>
  <integer>120</integer>
  <key>StandardOutPath</key>
  <string>$BASE/logs/launchd.notion-publication.out.log</string>
  <key>StandardErrorPath</key>
  <string>$BASE/logs/launchd.notion-publication.err.log</string>
</dict>
</plist>
PLIST
  plutil -lint "$temporary" >/dev/null
  mv "$temporary" "$TARGET"
  plutil -lint "$TARGET"
}

install_service() {
  write_plist
  run launchctl bootout "$DOMAIN" "$TARGET" >/dev/null 2>&1 || true
  run launchctl bootstrap "$DOMAIN" "$TARGET"
  run launchctl enable "$SERVICE"
}

uninstall_service() {
  run launchctl bootout "$DOMAIN" "$TARGET" >/dev/null 2>&1 || true
  if [ -e "$TARGET" ]; then
    run rm -f "$TARGET"
  fi
}

case "$command_name" in
  install) install_service ;;
  uninstall) uninstall_service ;;
  restart) uninstall_service; install_service ;;
  status) launchctl print "$SERVICE" ;;
  kickstart) run launchctl kickstart "$SERVICE" ;;
  *) usage >&2; exit 2 ;;
esac
