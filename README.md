# 🤖 Jarvis Voice Bridge

**A fully local, zero-cost voice assistant powered by open-source AI.**

Talk to your computer like Iron Man talks to Jarvis — except everything runs on your machine. No cloud APIs, no API keys, no subscriptions, full privacy.

[![Tests](https://github.com/escipionpedroza147-commits/jarvis-voice-bridge/actions/workflows/tests.yml/badge.svg)](https://github.com/escipionpedroza147-commits/jarvis-voice-bridge/actions/workflows/tests.yml)

## Architecture

```
┌─────────┐     ┌───────────────┐     ┌──────────┐     ┌─────────────┐     ┌──────────┐     ┌─────────┐
│   Mic   │────▶│  OpenWakeWord │────▶│ Recorder │────▶│faster-whisper│────▶│ Commands │────▶│  Ollama  │
│         │     │  "Jarvis"     │     │   VAD    │     │    STT      │     │  Parser  │     │   LLM    │
└─────────┘     └───────────────┘     └──────────┘     └─────────────┘     └──────────┘     └──────────┘
                                                                              │    │              │
                                                                    built-in ◀┘    └──▶ LLM ──────┘
                                                                    response              │
                                                                       │                  ▼
                                                                       └──────▶  🔊 TTS (Piper / say)
                                                                                     │
                                                                                     ▼
                                                                                  Speaker
```

1. **Wake word** — Listens for "Jarvis" using [OpenWakeWord](https://github.com/dscripka/openWakeWord)
2. **Record** — Captures your speech with energy-based voice activity detection
3. **Transcribe** — Converts speech to text with [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
4. **Command check** — Intercepts built-in voice commands (time, clear history, pause/resume)
5. **Think** — Sends the query to a local LLM via [Ollama](https://ollama.com)
6. **Speak** — Reads the response aloud with [Piper TTS](https://github.com/rhasspy/piper) or macOS `say`

## Features

- 🎙️ **Wake word detection** — Always-on "Jarvis" wake word with adjustable confidence
- 🧠 **Conversation memory** — Persists history to `~/.jarvis/history.json` across restarts
- 🗣️ **Multiple voices** — Choose from any macOS `say` voice (`--voice` flag or `JARVIS_VOICE` env)
- ⚡ **Built-in commands** — Time, clear history, pause/resume without LLM round-trip
- 📊 **Health monitoring** — HTTP `/health` and `/status` endpoints for uptime/stats
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
git clone https://github.com/escipionpedroza147-commits/jarvis-voice-bridge.git
cd jarvis-voice-bridge

# 2. Run setup (installs deps, creates venv)
bash setup.sh

# 3. Make sure Ollama is running with a model
ollama serve &
ollama pull qwen2.5-coder:7b

# 4. Start Jarvis
source .venv/bin/activate
python main.py
```

Then say **"Jarvis"** and ask a question!

## Voice Commands

Built-in commands are handled instantly without an LLM round-trip:

| Command | Action |
|---------|--------|
| "Jarvis, what time is it?" | Tells you the current time |
| "Jarvis, clear history" | Resets conversation memory |
| "Jarvis, stop listening" | Pauses wake word detection |
| "Jarvis, resume" | Resumes after pause |

Alternative phrases also work: "reset conversation", "erase memory", "pause", "go to sleep", "wake up", "start listening", etc.

## Configuration

All settings can be overridden via environment variables or a `.env` file. Copy `.env.example` to `.env` to get started.

| Variable | Default | Description |
|----------|---------|-------------|
| `JARVIS_WAKE_WORD` | `jarvis` | Wake word to listen for |
| `JARVIS_WAKE_CONFIDENCE` | `0.5` | Minimum detection confidence (0.0–1.0) |
| `JARVIS_OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `JARVIS_OLLAMA_MODEL` | `qwen2.5-coder:7b` | Ollama model to use |
| `JARVIS_OLLAMA_TIMEOUT` | `120` | LLM request timeout (seconds) |
| `JARVIS_WHISPER_MODEL` | `base.en` | Whisper model size |
| `JARVIS_WHISPER_DEVICE` | `cpu` | Compute device (`cpu` or `cuda`) |
| `JARVIS_WHISPER_COMPUTE` | `int8` | Quantisation type |
| `JARVIS_SAMPLE_RATE` | `16000` | Audio sample rate |
| `JARVIS_SILENCE_MS` | `500` | Silence duration to stop recording (ms) |
| `JARVIS_SILENCE_ENERGY` | `0.01` | RMS energy threshold for silence |
| `JARVIS_MAX_RECORD_SEC` | `30` | Maximum recording duration |
| `JARVIS_TTS_BACKEND` | `auto` | TTS: `auto`, `piper`, or `say` |
| `JARVIS_PIPER_VOICE` | `en_US-lessac-medium` | Piper voice model |
| `JARVIS_SAY_VOICE` | `Daniel` | macOS say voice |
| `JARVIS_VOICE` | *(none)* | Override voice (highest priority) |
| `JARVIS_MAX_HISTORY` | `10` | LLM context history pairs |
| `JARVIS_HISTORY_FILE` | `~/.jarvis/history.json` | Persistent history file path |
| `JARVIS_HISTORY_MAX_ENTRIES` | `200` | Max entries in history file |
| `JARVIS_HEALTH_PORT` | `8765` | Health/status HTTP port |
| `JARVIS_SYSTEM_PROMPT` | *(built-in)* | LLM system prompt |
| `JARVIS_LOG_LEVEL` | `INFO` | Logging level |
| `JARVIS_BLIP_FREQ` | `880` | Activation beep frequency (Hz) |
| `JARVIS_BLIP_DURATION_MS` | `150` | Activation beep duration (ms) |

### CLI Options

```bash
python main.py --model llama3:8b          # Use a different Ollama model
python main.py --wake-word jarvis          # Change wake word
python main.py --whisper-model small.en    # Use a larger Whisper model
python main.py --tts say                   # Force macOS say TTS
python main.py --voice Daniel              # Use a specific voice
python main.py --list-voices               # List available macOS voices
python main.py --log-level DEBUG           # Verbose logging
```

## Health & Status Endpoint

Jarvis exposes a lightweight HTTP server for monitoring (default port: 8765).

### GET /health

```json
{"status": "ok"}
```

### GET /status

```json
{
  "status": "running",
  "uptime_seconds": 3600.5,
  "total_queries": 42,
  "last_query": "What's the weather?",
  "last_query_time": 1703275200.0,
  "wake_word_count": 45,
  "model": "qwen2.5-coder:7b"
}
```

### Usage

```bash
# Quick health check
curl http://localhost:8765/health

# Full status
curl http://localhost:8765/status | python -m json.tool
```

## Conversation Memory

Jarvis remembers conversations across restarts. History is stored in `~/.jarvis/history.json` by default.

- **Max entries**: 200 (configurable via `JARVIS_HISTORY_MAX_ENTRIES`)
- **Clear via voice**: "Jarvis, clear history"
- **Clear manually**: Delete `~/.jarvis/history.json`
- **Custom path**: Set `JARVIS_HISTORY_FILE`

## Docker

> ⚠️ **Audio I/O does not work in Docker containers.** The Docker setup is intended for API-mode use, testing, and accessing the health/status endpoint.

### Build & Run

```bash
# Build
docker build -t jarvis-voice-bridge .

# Run (health endpoint accessible on port 8765)
docker run -p 8765:8765 --env-file .env jarvis-voice-bridge

# With Docker Compose (includes Ollama)
docker compose up

# Run tests in Docker
docker compose run tests
```

## Project Structure

```
jarvis-voice-bridge/
├── main.py              # Entry point
├── config.py            # Configuration (env vars / .env)
├── src/
│   ├── wake_word.py     # OpenWakeWord listener
│   ├── recorder.py      # Audio recorder with VAD
│   ├── stt.py           # faster-whisper transcription
│   ├── llm.py           # Ollama HTTP client
│   ├── tts.py           # Piper TTS + macOS say fallback + voice listing
│   ├── commands.py      # Built-in voice command parser
│   ├── memory.py        # Conversation history persistence
│   ├── health.py        # Health/status HTTP server
│   ├── audio.py         # Audio playback utilities
│   └── pipeline.py      # Full pipeline orchestration
├── tests/               # pytest test suite (136+ tests)
├── .github/workflows/   # CI/CD (GitHub Actions)
├── Dockerfile           # Container image
├── docker-compose.yml   # Multi-container setup
├── requirements.txt
├── setup.sh             # One-command setup
├── .env.example         # Configuration template
├── CONTRIBUTING.md      # Contribution guidelines
├── LICENSE              # MIT
└── README.md
```

## Running Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

Tests mock all hardware (microphone, speaker) and network calls, so they run anywhere without audio devices or a running Ollama instance.

**Current test count: 136+**

## How It Works

### Wake Word Detection
The system continuously listens to your microphone using OpenWakeWord. When it detects "Jarvis" with sufficient confidence, it plays a short activation beep and starts recording.

### Voice Activity Detection
After activation, recording uses a simple energy-based VAD. It monitors the RMS energy of incoming audio chunks and stops recording after 500ms of silence (configurable). A 30-second hard limit prevents runaway recordings.

### Speech-to-Text
Recorded audio is transcribed locally using faster-whisper with the `base.en` model. The model loads once at startup and is reused for all subsequent transcriptions.

### Command Parsing
Before sending transcribed text to the LLM, Jarvis checks for built-in voice commands. Commands like "what time is it" and "clear history" are handled instantly without a network round-trip.

### LLM Processing
The transcribed text is sent to a local Ollama instance. The client maintains a rolling conversation history (last 10 exchanges by default) for multi-turn context. Responses are streamed for lower perceived latency.

### Text-to-Speech
On ARM64 Mac where piper-tts isn't available, the system automatically falls back to the built-in macOS `say` command. Multiple voices are supported — use `--list-voices` to see what's available on your system.

### Health Monitoring
A lightweight HTTP server runs on port 8765, exposing `/health` and `/status` endpoints. Useful for monitoring dashboards, automation, and ensuring Jarvis is responsive.

## Troubleshooting

### "Cannot open microphone input stream"
- Make sure your terminal app has microphone permission (System Settings → Privacy & Security → Microphone)
- Check that portaudio is installed: `brew install portaudio`

### "Cannot connect to Ollama"
- Start Ollama: `ollama serve`
- Pull a model: `ollama pull qwen2.5-coder:7b`
- Verify: `curl http://localhost:11434/api/tags`

### "No TTS backend available"
- On macOS ARM64, the `say` command should always be available
- If using piper-tts on ARM64, you may need to build from source

### Wake word not detecting
- Speak clearly and at a normal volume
- Try lowering the confidence threshold: `JARVIS_WAKE_CONFIDENCE=0.3`
- Check your mic input level in System Settings → Sound

### Slow responses
- Use a smaller Ollama model: `--model qwen2.5-coder:3b`
- Use a smaller Whisper model: `--whisper-model tiny.en`
- Make sure you have enough free RAM (8GB+ recommended)

### Health endpoint not responding
- Check port availability: `lsof -i :8765`
- Try a different port: `JARVIS_HEALTH_PORT=9090`

## License

MIT — see [LICENSE](LICENSE).

## Author

**Escipion Pedroza** — [GitHub](https://github.com/escipionpedroza147-commits)
