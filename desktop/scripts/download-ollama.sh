#!/bin/bash
# Download Ollama binary for bundling in Nova desktop app
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$SCRIPT_DIR/../bin"
mkdir -p "$BIN_DIR"

echo "⬇️  Downloading Ollama for macOS (universal)..."
curl -sL "https://github.com/ollama/ollama/releases/download/v0.24.0/ollama-darwin.tgz" -o /tmp/ollama-darwin.tgz
echo "📦 Extracting..."
tar -xzf /tmp/ollama-darwin.tgz -C "$BIN_DIR"
rm -f /tmp/ollama-darwin.tgz

echo "✅ Ollama binary ready at $BIN_DIR/ollama"
ls -lh "$BIN_DIR/ollama"
