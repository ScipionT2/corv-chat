#!/usr/bin/env bash
# Uninstall EP Agent LaunchAgent (disable auto-start on login).
#
# Usage:
#   bash scripts/uninstall-launchagent.sh

set -euo pipefail

PLIST_LABEL="com.ep-agent"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

if [ ! -f "$PLIST_PATH" ]; then
    echo "ℹ️  LaunchAgent not installed (nothing to remove)"
    exit 0
fi

# Unload the agent
launchctl unload "$PLIST_PATH" 2>/dev/null || true

# Remove the plist file
rm -f "$PLIST_PATH"

echo "✅ LaunchAgent removed: $PLIST_PATH"
echo "   EP Agent will no longer start automatically on login."
