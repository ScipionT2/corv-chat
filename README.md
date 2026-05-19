<p align="center">
  <img src="website/nova-logo.jpg" alt="Nova" width="120" style="border-radius:20px">
</p>

# ⚡ Nova

**A fully local, zero-cost multimodal voice assistant powered by open-source AI.**

Talk to your computer naturally — everything runs on your machine. No cloud APIs, no API keys, no subscriptions, full privacy.

[![Tests](https://github.com/escipionpedroza147-commits/nova/actions/workflows/tests.yml/badge.svg)](https://github.com/escipionpedroza147-commits/nova/actions/workflows/tests.yml)

## Architecture

```
┌─────────┐     ┌───────────────┐     ┌──────────┐     ┌─────────────┐     ┌──────────┐     ┌─────────┐
│   Mic   │────▶│  OpenWakeWord │────▶│ Recorder │────▶│faster-whisper│────▶│ Commands │────▶│  Ollama  │
│         │     │  (wake word)  │     │   VAD    │     │    STT      │     │  Parser  │     │   LLM    │
└─────────┘     └───────────────┘     └──────────┘     └─────────────┘     └──────────┘     └──────────┘
                                                                              │    │              │
                                                                    built-in ◀┘    └──▶ LLM ──────┘
                                                                    response              │
                                                                       │                  ▼
                                                                       └──────▶  🔊 TTS (Piper / say)
                                                                                     │
                                                                                     ▼
                                                                                  Speaker

┌────────────────────────────────────────────────────────────────────────────────────────────┐
│  🖥 Vision (event-driven)        │  📱 Menu Bar (macOS)        │  📊 Health Server         │
│  Screen change detection         │  Status indicator           │  /health, /status         │
│  Ollama multimodal inference     │  Start/Stop/Vision/Quit     │  HTTP JSON endpoints      │
│  Sleep/wake cycle (<2% idle CPU) │  Toggle overlay panel       │  Port 8765                │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

1. **Wake word** — Listens for "Nova" using [OpenWakeWord](https://github.com/dscripka/openWakeWord)
2. **Record** — Captures speech with energy-based voice activity detection
3. **Transcribe** — Converts speech to text with [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
4. **Command check** — Intercepts built-in voice commands (time, clear history, pause/resume, vision)
5. **Think** — Sends the query to a local LLM via [Ollama](https://ollama.com)
6. **Speak** — Reads the response aloud with [Piper TTS](https://github.com/rhasspy/piper) or macOS `say`
7. **Vision** — Event-driven screen analysis (only when content changes)
8. **Menu Bar** — Lightweight macOS system tray for status + controls

## Features

- 🎙️ **Wake word detection** — "Nova" wake word with adjustable confidence
- 👁 **Event-driven vision** — Screen analysis only on change (sleep/wake cycle, <2% idle CPU)
- 📱 **Menu bar app** — macOS system tray with status, controls, and overlay toggle
- 🧠 **Conversation memory** — Persists history to `~/.nova/history.json`
- 🗣️ **Multiple voices** — Choose from any macOS `say` voice (`--voice` flag or `NOVA_SAY_VOICE` env)
- ⚡ **Built-in commands** — Time, clear history, pause/resume, screen analysis without LLM round-trip
- 📊 **Health monitoring** — HTTP `/health` and `/status` endpoints for uptime/stats
- 🔋 **Resource optimized** — 25% CPU thread cap, KV cache flushing, low process priority
- 🔒 **100% local** — No cloud, no API keys, no data leaves your machine
- 🐳 **Docker support** — Containerized for API-mode and testing

## Prerequisites

| Requirement | Version | Notes |
|------------|---------|-------|
| Python | 3.10+ | 3.12+ recommended |
| Ollama | Latest | [Install](https://ollama.com/download) |
| Homebrew | Latest | For portaudio/ffmpeg |
| macOS | ARM64 | Apple Silicon (M1/M2/M3/M4) |

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/escipionpedroza147-commits/nova.git
cd nova

# 2. Run setup (installs deps, creates venv)
bash setup.sh

# 3. Make sure Ollama is running with a model
ollama serve &
ollama pull qwen2.5:3b

# 4. Start Nova
source .venv/bin/activate
python nova.py

# Check system readiness first:
python nova.py --check
```

Then say **"Nova"** and ask a question!

### Launch Options

```bash
# Control Center (recommended — GUI launcher)
python launcher.py                    # Opens Control Center dashboard

# Direct launch (terminal users)
python nova.py                    # Full experience (menu bar + voice)
python nova.py --no-overlay       # Voice only, no GUI at all
python nova.py --vision-only      # Vision + menu bar only
python nova.py --check            # Check system readiness
python nova.py --log-level DEBUG  # Verbose logging
```

## Control Center

The Control Center is a compact dashboard window that lets you manage Nova visually:

- **Start/Stop** the full pipeline with one click
- **Status dashboard** — Ollama, models, voice status with colored indicators
- **Quick settings** — theme (dark/light), accent color, voice selector
- **Open Sidebar** — launch the sidebar panel on demand
- **Menu bar icon** — always visible with status dot (green/yellow/red)
- **Start on Login** — toggle auto-start via macOS LaunchAgent

Launch via `python launcher.py` or the `.app` bundle.

## Building the .app Bundle

Build a native macOS `.app` that shows in Dock:

```bash
# Install build dependencies
pip install -r requirements-dev.txt

# Build the .app
bash scripts/build-app.sh

# Install to /Applications
cp -r "dist/Nova.app" /Applications/
```

The `.app` launches the Control Center on startup. From there you can start the full Nova experience.

## Voice Commands

Built-in commands are handled instantly without an LLM round-trip:

| Command | Action |
|---------|--------|
| "What time is it?" | Tells you the current time |
| "Clear history" | Resets conversation memory |
| "Stop listening" / "Go to sleep" | Pauses wake word detection |
| "Resume" / "Wake up" | Resumes after pause |
| "Analyze my screen" / "What do you see?" | One-shot screen analysis |
| "Start analysis" / "Toggle analysis" | Continuous screen monitoring |
| "Shutdown" / "Goodbye" | Terminates Nova |

## Vision System

Nova includes an **event-driven** vision system that monitors your screen intelligently:

- **Change detection**: Captures frames and computes pixel diffs (very cheap)
- **Threshold**: Only invokes the vision model when >15% of pixels change
- **Sleep/wake**: Enters deep sleep after 30s of no change, wakes on voice trigger or screen change
- **Result**: <2% CPU idle vs. ~30% with constant polling

Enable with: `NOVA_VISION=true` or use the menu bar toggle.

## Menu Bar App (macOS)

The menu bar extra provides lightweight status + controls:

- 🟢 Idle | 🎤 Listening | ⚡ Processing | 🔊 Speaking | 👁 Analyzing | 💤 Sleeping
- Start/Stop pipeline
- Toggle vision analysis
- Show/hide overlay panel
- Quit

Enable with: `NOVA_MENUBAR=true` (default).

## Configuration

All settings use the `NOVA_*` prefix. Legacy `EP_*` and `JARVIS_*` env vars still work as fallbacks.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOVA_WAKE_WORD` | `nova` | Wake word to listen for |
| `NOVA_WAKE_CONFIDENCE` | `0.5` | Detection confidence (0.0–1.0) |
| `NOVA_OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `NOVA_OLLAMA_MODEL` | `qwen2.5:3b` | Ollama model |
| `NOVA_OLLAMA_TIMEOUT` | `120` | LLM timeout (seconds) |
| `NOVA_WHISPER_MODEL` | `base.en` | Whisper model size |
| `NOVA_WHISPER_DEVICE` | `auto` | Compute device |
| `NOVA_WHISPER_COMPUTE` | `int8` | Quantisation type |
| `NOVA_SILENCE_MS` | `800` | Silence duration to stop recording (ms) |
| `NOVA_MAX_RECORD_SEC` | `30` | Maximum recording duration |
| `NOVA_TTS_BACKEND` | `auto` | TTS: `auto`, `piper`, or `say` |
| `NOVA_SAY_VOICE` | `Daniel` | macOS say voice |
| `NOVA_MAX_HISTORY` | `10` | LLM context history pairs |
| `NOVA_HISTORY_FILE` | `~/.nova/history.json` | Persistent history path |
| `NOVA_HEALTH_PORT` | `8765` | Health/status HTTP port |
| `NOVA_VISION` | `false` | Enable vision subsystem |
| `NOVA_VISION_MODEL` | `moondream:1.8b` | Ollama vision model |
| `NOVA_VISION_INTERVAL` | `10.0` | Screen check interval (seconds) |
| `NOVA_VISION_CHANGE_THRESHOLD` | `0.15` | Min pixel change ratio to trigger model |
| `NOVA_VISION_SLEEP_TIMEOUT` | `30.0` | Seconds before entering deep sleep |
| `NOVA_OVERLAY` | `false` | Enable side-panel overlay |
| `NOVA_MENUBAR` | `true` | Enable menu bar extra |
| `NOVA_KV_FLUSH_MINUTES` | `10` | KV cache flush interval |
| `NOVA_MAX_CPU_PERCENT` | `25.0` | Soft CPU cap |
| `NOVA_LOG_LEVEL` | `INFO` | Logging level |

## Resource Management

Nova is optimized for background operation:

- **CPU thread cap**: 25% of cores (via `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OLLAMA_NUM_THREAD`)
- **Low process priority**: `nice 10` prevents OS freezing
- **KV cache flushing**: Every 10 minutes (idle only) to prevent VRAM leaks
- **Event-driven vision**: Only uses GPU when screen content changes
- **Menu bar over overlay**: Eliminates constant GPU draw calls

Target: **<10% CPU idle, <2GB RAM idle**.

## LaunchAgent (Auto-start)

A macOS LaunchAgent is installed at `~/Library/LaunchAgents/com.nova.plist`:

```bash
# Load/start
launchctl load ~/Library/LaunchAgents/com.nova.plist

# Unload/stop
launchctl unload ~/Library/LaunchAgents/com.nova.plist

# Check status
launchctl list | grep nova
```

## Health & Status Endpoint

Nova exposes a lightweight HTTP server for monitoring (default port: 8765).

```bash
curl http://localhost:8765/health   # {"status": "ok"}
curl http://localhost:8765/status   # Full status JSON
```

## Project Structure

```
nova/
├── nova.py              # Main entry point (multimodal launcher)
├── config.py            # Configuration (NOVA_* env vars with EP_*/JARVIS_* fallback)
├── main.py              # Legacy CLI entry point
├── jarvis_multimodal.py # Backward-compat shim → nova.py
├── src/
│   ├── pipeline.py      # Full pipeline orchestration (NovaPipeline)
│   ├── wake_word.py     # OpenWakeWord listener
│   ├── recorder.py      # Audio recorder with VAD
│   ├── stt.py           # faster-whisper transcription
│   ├── llm.py           # Ollama HTTP client
│   ├── tts.py           # Piper TTS + macOS say fallback
│   ├── commands.py      # Built-in voice command parser
│   ├── vision.py        # Event-driven screen analysis (change detection)
│   ├── menubar.py       # macOS menu bar extra (rumps)
│   ├── overlay.py       # Optional side-panel overlay (PyQt6)
│   ├── dock_glow.py     # Dock glow indicator
│   ├── memory.py        # Conversation history persistence
│   ├── health.py        # Health/status HTTP server
│   ├── audio.py         # Audio playback utilities
│   └── resource_manager.py # CPU/memory throttling, KV cache, thread limiting
├── tests/               # pytest test suite (136+ tests)
├── .github/workflows/   # CI/CD (GitHub Actions)
├── Dockerfile           # Container image
├── docker-compose.yml   # Multi-container setup
├── requirements.txt
├── setup.sh             # One-command setup
└── README.md
```

## Running Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

Tests mock all hardware and network calls — no audio devices or Ollama required.

## Troubleshooting

### "Cannot open microphone input stream"
- Grant microphone permission (System Settings → Privacy & Security → Microphone)
- Install portaudio: `brew install portaudio`

### "Cannot connect to Ollama"
- Start Ollama: `ollama serve`
- Pull a model: `ollama pull qwen2.5:3b`
- Verify: `curl http://localhost:11434/api/tags`

### Wake word not detecting
- Speak clearly at normal volume
- Lower confidence: `NOVA_WAKE_CONFIDENCE=0.3`
- Check mic input level in System Settings → Sound

### Slow responses
- Use a smaller model: `NOVA_OLLAMA_MODEL=qwen2.5:1.5b`
- Use a smaller Whisper: `NOVA_WHISPER_MODEL=tiny.en`
- Ensure 8GB+ free RAM

## 🔒 Privacy

- **Zero cloud dependencies** — Everything runs on-device
- **No telemetry** — No analytics, no phone-home
- **No account required** — Clone and run
- **Air-gap compatible** — Works fully offline once models are downloaded
- **History under your control** — Stored in `~/.nova/history.json`, delete anytime

## Suite Integration

Nova is part of the **Escipion AI Business Suite**:

- **[API Sentinel](https://github.com/escipionpedroza147-commits/API-Sentinel)** — Monitors OpenAI API spend, tracks tokens, fires budget alerts
- **[Token Treasury](https://github.com/escipionpedroza147-commits/Token-Treasury)** — Analyzes Sentinel data, generates Cost-Efficiency Scores
- **Nova** — Voice-driven interface, reads Treasury reports aloud
- **Master Controller** — Orchestration layer connecting all three (coming soon)

## License

MIT — see [LICENSE](LICENSE).

## Author

**Escipion Pedroza** — [GitHub](https://github.com/escipionpedroza147-commits)
