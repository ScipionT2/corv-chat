#!/usr/bin/env bash
# Install EP Agent LaunchAgent for auto-start on login.
#
# Usage:
#   bash scripts/install-launchagent.sh
#
# This creates ~/Library/LaunchAgents/com.ep-agent.plist

set -euo pipefail

PLIST_LABEL="com.ep-agent"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Determine the best program to launch
APP_PATH="/Applications/EP Agent.app"
if [ -d "$APP_PATH" ]; then
    PROGRAM_ARGS="<string>/usr/bin/open</string>
    <string>-a</string>
    <string>${APP_PATH}</string>"
else
    # Fall back to running the Python launcher directly
    PYTHON="${PROJECT_ROOT}/.venv/bin/python"
    LAUNCHER="${PROJECT_ROOT}/launcher.py"
    PROGRAM_ARGS="<string>${PYTHON}</string>
    <string>${LAUNCHER}</string>"
fi

# Create LaunchAgents directory if needed
mkdir -p "$HOME/Library/LaunchAgents"

# Write the plist
cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
    ${PROGRAM_ARGS}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>WorkingDirectory</key>
    <string>${PROJECT_ROOT}</string>
    <key>StandardOutPath</key>
    <string>${HOME}/.ep-agent/launcher.log</string>
    <key>StandardErrorPath</key>
    <string>${HOME}/.ep-agent/launcher-error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF

# Load the agent
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "✅ LaunchAgent installed: $PLIST_PATH"
echo "   EP Agent will start automatically on login."
echo ""
echo "   To unload: launchctl unload $PLIST_PATH"
echo "   To remove: bash scripts/uninstall-launchagent.sh"
