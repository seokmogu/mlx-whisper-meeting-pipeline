#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LABEL="${VOICE_MEMO_LAUNCHD_LABEL:-com.seokmogu.voicememo-local-pipeline}"
DOMAIN="gui/$(id -u)"
SERVICE="$DOMAIN/$LABEL"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
VOICE_MEMOS_DIR="$HOME/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
DRY_RUN=0
MARK_EXISTING=1

usage() {
  cat <<'USAGE'
Usage: local-launchd.sh <install|uninstall|restart|status|kickstart> [--dry-run] [--no-mark-existing]

Manage the local Voice Memos launchd job. The installed job runs
sh/run-local-pipeline.sh and never uploads to Notion.

Commands:
  install     Write plist, mark current Voice Memos as seen, and bootstrap the job.
  uninstall   Boot out the job and remove the plist.
  restart     uninstall + install.
  status      Print launchd service state.
  kickstart   Trigger the installed job now.

Options:
  --dry-run           Print actions without changing launchd state or files.
  --no-mark-existing  Do not baseline current Voice Memos during install.
USAGE
}

command_name="${1:-}"
if [ -n "$command_name" ]; then
  shift
fi

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --no-mark-existing)
      MARK_EXISTING=0
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
  run mkdir -p "$(dirname "$TARGET")" "$BASE/logs"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry-run: write $TARGET"
    return
  fi
  tmp="$(mktemp)"
  cat > "$tmp" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$BASE/sh/run-local-pipeline.sh</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>MEETING_BASE_DIR</key>
        <string>$BASE</string>
        <key>VOICE_MEMO_USE_SEEN_STATE</key>
        <string>1</string>
    </dict>

    <key>WatchPaths</key>
    <array>
        <string>$VOICE_MEMOS_DIR</string>
    </array>

    <key>StartInterval</key>
    <integer>120</integer>

    <key>RunAtLoad</key>
    <false/>

    <key>ThrottleInterval</key>
    <integer>120</integer>

    <key>StandardOutPath</key>
    <string>$BASE/logs/launchd.local.out.log</string>

    <key>StandardErrorPath</key>
    <string>$BASE/logs/launchd.local.err.log</string>
</dict>
</plist>
PLIST
  plutil -lint "$tmp" >/dev/null
  mv "$tmp" "$TARGET"
  plutil -lint "$TARGET"
}

install_service() {
  write_plist
  if [ "$MARK_EXISTING" -eq 1 ]; then
    run "$BASE/sh/sync-voice-memos.sh" --mark-existing
  fi
  run launchctl bootout "$DOMAIN" "$TARGET" >/dev/null 2>&1 || true
  run launchctl bootstrap "$DOMAIN" "$TARGET"
  run launchctl enable "$SERVICE"
  echo "installed: $TARGET"
}

uninstall_service() {
  run launchctl bootout "$DOMAIN" "$TARGET" >/dev/null 2>&1 || true
  run rm -f "$TARGET"
  echo "uninstalled: $TARGET"
}

case "$command_name" in
  install)
    install_service
    ;;
  uninstall)
    uninstall_service
    ;;
  restart)
    uninstall_service
    install_service
    ;;
  status)
    run launchctl print "$SERVICE"
    ;;
  kickstart)
    # `launchctl kickstart` can wait behind launchd throttling after rapid
    # retries. The legacy start command returns promptly for a loaded user
    # LaunchAgent and is enough for manual verification.
    run launchctl start "$LABEL"
    ;;
  -h|--help|"")
    usage
    [ -n "$command_name" ] || exit 2
    ;;
  *)
    echo "Unknown command: $command_name" >&2
    usage >&2
    exit 2
    ;;
esac
