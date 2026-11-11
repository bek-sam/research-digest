#!/bin/bash
# Installs the daily (07:00) and bot launchd jobs for this checkout. The checkout must not be under ~/Desktop,
# ~/Documents or ~/Downloads: macOS blocks launchd jobs from reading those folders.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
for j in daily bot; do
  dst=~/Library/LaunchAgents/com.researchdigest.$j.plist
  launchctl unload "$dst" 2>/dev/null || true
  sed "s#__ROOT__#$ROOT#g" "$ROOT/deploy/com.researchdigest.$j.plist" > "$dst"
  launchctl load "$dst"
done
echo "installed for $ROOT"
