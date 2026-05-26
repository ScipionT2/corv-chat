#!/bin/bash
# Nova AI — One-line installer for macOS
# Usage: curl -fsSL https://nov-assistant.com/install.sh | bash

set -e

BOLD="\033[1m"
GREEN="\033[32m"
CYAN="\033[36m"
PURPLE="\033[35m"
DIM="\033[2m"
RESET="\033[0m"

echo ""
echo -e "${PURPLE}${BOLD}  ⚡ Nova AI Installer${RESET}"
echo -e "${DIM}  Personal AI Operating System${RESET}"
echo ""

# Check macOS
if [[ "$(uname)" != "Darwin" ]]; then
  echo "❌ Nova desktop app currently supports macOS only."
  echo "   Visit https://nov-assistant.com for the web version."
  exit 1
fi

# Check architecture
ARCH=$(uname -m)
if [[ "$ARCH" != "arm64" ]]; then
  echo "⚠️  Nova is optimized for Apple Silicon (M1/M2/M3/M4)."
  echo "   Intel Macs can use the web version at https://nov-assistant.com"
  exit 1
fi

# Get latest release URL
echo -e "${CYAN}→${RESET} Finding latest release..."
RELEASE_URL=$(curl -sI "https://github.com/escipionpedroza147-commits/Nova/releases/latest" | grep -i "location:" | awk '{print $2}' | tr -d '\r')
TAG=$(basename "$RELEASE_URL")
DMG_URL="https://github.com/escipionpedroza147-commits/Nova/releases/download/${TAG}/Nova.AI-${TAG#v}-arm64.dmg"

echo -e "${CYAN}→${RESET} Downloading Nova AI ${TAG}..."
TMPDIR=$(mktemp -d)
DMG_PATH="${TMPDIR}/Nova-AI.dmg"

curl -fsSL "$DMG_URL" -o "$DMG_PATH" --progress-bar

echo -e "${CYAN}→${RESET} Installing..."
# Mount DMG
hdiutil attach "$DMG_PATH" -quiet -nobrowse -mountpoint "${TMPDIR}/nova-mount"

# Copy to Applications
if [ -d "/Applications/Nova AI.app" ]; then
  echo -e "${DIM}   Removing old version...${RESET}"
  rm -rf "/Applications/Nova AI.app"
fi
cp -R "${TMPDIR}/nova-mount/Nova AI.app" "/Applications/"

# Cleanup
hdiutil detach "${TMPDIR}/nova-mount" -quiet
rm -rf "$TMPDIR"

echo ""
echo -e "${GREEN}${BOLD}  ✅ Nova AI installed!${RESET}"
echo ""
echo -e "  ${BOLD}Open from:${RESET}"
echo -e "    • Spotlight: ${CYAN}⌘ Space${RESET} → type ${BOLD}Nova${RESET}"
echo -e "    • Finder: ${CYAN}/Applications/Nova AI.app${RESET}"
echo -e "    • Terminal: ${CYAN}open '/Applications/Nova AI.app'${RESET}"
echo ""
echo -e "  ${DIM}First launch will download a small AI model (~2GB).${RESET}"
echo -e "  ${DIM}Web version: https://nov-assistant.com${RESET}"
echo ""
