#!/usr/bin/env bash
# Build the Nova .app bundle using py2app.
#
# Usage:
#   bash scripts/build-app.sh
#
# Prerequisites:
#   pip install py2app
#   (or: pip install -r requirements-dev.txt)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "⚡ Building Nova .app bundle..."
echo ""

# ─── Check dependencies ──────────────────────────────────────────────────────
if ! python -c "import py2app" 2>/dev/null; then
    echo "❌ py2app not installed. Run:"
    echo "   pip install -r requirements-dev.txt"
    exit 1
fi

# ─── Generate app icon if missing ────────────────────────────────────────────
if [ ! -f "assets/AppIcon.icns" ]; then
    echo "📦 Generating app icon..."
    python scripts/generate_icon.py
fi

# ─── Clean previous builds ───────────────────────────────────────────────────
echo "🧹 Cleaning previous builds..."
rm -rf build/ dist/

# ─── Build ────────────────────────────────────────────────────────────────────
echo "🔨 Running py2app..."
python setup_app.py py2app

# ─── Verify ──────────────────────────────────────────────────────────────────
APP_PATH="dist/Nova.app"
if [ -d "$APP_PATH" ]; then
    echo ""
    echo "✅ Build successful!"
    echo "   App: $APP_PATH"
    echo ""
    echo "   To run:  open \"$APP_PATH\""
    echo "   To install: cp -r \"$APP_PATH\" /Applications/"
    echo ""
    # Show size
    SIZE=$(du -sh "$APP_PATH" | cut -f1)
    echo "   Size: $SIZE"
else
    echo ""
    echo "❌ Build failed — no .app found in dist/"
    exit 1
fi
