#!/bin/bash
#
# Nova — One-command setup
#
# Usage: bash setup.sh
#
set -e

echo "⚡ Nova — Setup"
echo "======================="
echo ""

# -------------------------------------------------------
# 1. System dependencies (macOS / Homebrew)
# -------------------------------------------------------
if command -v brew &> /dev/null; then
    echo "📦 Installing system deps via Homebrew …"
    brew install portaudio ffmpeg 2>/dev/null || true
else
    echo "⚠️  Homebrew not found — please install portaudio and ffmpeg manually."
fi

# -------------------------------------------------------
# 2. Python virtual environment
# -------------------------------------------------------
if [ ! -d ".venv" ]; then
    echo "🐍 Creating Python virtual environment …"
    python3 -m venv .venv
fi

echo "🐍 Activating venv …"
source .venv/bin/activate

# -------------------------------------------------------
# 3. Python dependencies
# -------------------------------------------------------
echo "📦 Installing Python dependencies …"
pip install --upgrade pip -q
pip install -r requirements.txt -q

# -------------------------------------------------------
# 4. Model info
# -------------------------------------------------------
echo ""
echo "📂 Models will download automatically on first run."
echo "   Whisper model: base.en (~150 MB)"
echo "   OpenWakeWord: ~10 MB"
echo ""

# -------------------------------------------------------
# 5. Check Ollama
# -------------------------------------------------------
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "✅ Ollama is running"
    # Show available models
    echo "   Available models:"
    curl -s http://localhost:11434/api/tags | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for m in data.get('models', []):
        print(f\"     - {m['name']}\")
except: pass
" 2>/dev/null || true
else
    echo "⚠️  Ollama not running. Start it with:"
    echo "     ollama serve"
    echo ""
    echo "   Then pull a model:"
    echo "     ollama pull qwen2.5-coder:7b"
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "   To run Nova:"
echo "     source .venv/bin/activate"
echo "     python nova.py"
echo ""
echo "   Options:"
echo "     python nova.py --model llama3:8b"
echo "     python nova.py --wake-word nova"
echo "     python nova.py --whisper-model small.en"
echo "     python nova.py --tts say"
echo ""
