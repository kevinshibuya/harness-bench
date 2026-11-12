#!/bin/bash
# install-launchd.sh — install or reload the nightly extract-and-report agent. Idempotent.
#
#   install-launchd.sh            install or reload
#   install-launchd.sh --remove   unload and delete
#
# Fire it by hand:  launchctl start com.kevin.harness-bench
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.kevin.harness-bench
DST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--remove" ]; then
  rm -f "$DST"
  echo "install-launchd: removed $LABEL"
  exit 0
fi

mkdir -p "$(dirname "$DST")" "$ROOT/out"
cat >"$DST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/bin/nightly.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>$ROOT/out/extract.log</string>
  <key>StandardErrorPath</key><string>$ROOT/out/extract.log</string>
</dict>
</plist>
PLIST
plutil -lint "$DST" >/dev/null
launchctl bootstrap "$DOMAIN" "$DST"
echo "install-launchd: loaded $LABEL"
