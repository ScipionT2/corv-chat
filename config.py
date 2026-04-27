"""
Configuration module for Jarvis Voice Bridge.

All settings are configurable via environment variables or a .env file.
Sensible defaults are provided for a zero-config startup experience.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root if it exists
_PROJECT_ROOT = Path(__file__).parent
_ENV_PATH = _PROJECT_ROOT / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)


def _get_env(key: str, default: str) -> str:
    """Get an environment variable with a fallback default."""
    return os.environ.get(key, default)


def _get_env_int(key: str, default: int) -> int:
    """Get an integer environment variable with a fallback default."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_env_float(key: str, default: float) -> float:
    """Get a float environment variable with a fallback default."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Wake word
# ---------------------------------------------------------------------------
WAKE_WORD: str = _get_env("JARVIS_WAKE_WORD", "jarvis")
WAKE_WORD_CONFIDENCE: float = _get_env_float("JARVIS_WAKE_CONFIDENCE", 0.5)

# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
SAMPLE_RATE: int = _get_env_int("JARVIS_SAMPLE_RATE", 16000)
CHANNELS: int = 1
AUDIO_CHUNK_SAMPLES: int = _get_env_int("JARVIS_CHUNK_SAMPLES", 1280)
"""Number of samples per audio chunk (1280 ≈ 80 ms at 16 kHz)."""

# ---------------------------------------------------------------------------
# Recorder / VAD
# ---------------------------------------------------------------------------
SILENCE_THRESHOLD_MS: int = _get_env_int("JARVIS_SILENCE_MS", 500)
"""Milliseconds of silence before recording stops."""

SILENCE_ENERGY_THRESHOLD: float = _get_env_float("JARVIS_SILENCE_ENERGY", 0.01)
"""RMS energy below this value is considered silence."""

MAX_RECORD_SECONDS: int = _get_env_int("JARVIS_MAX_RECORD_SEC", 30)
"""Safety cutoff for a single recording."""

# ---------------------------------------------------------------------------
# Speech-to-Text (faster-whisper)
# ---------------------------------------------------------------------------
WHISPER_MODEL: str = _get_env("JARVIS_WHISPER_MODEL", "base.en")
WHISPER_DEVICE: str = _get_env("JARVIS_WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE: str = _get_env("JARVIS_WHISPER_COMPUTE", "int8")

# ---------------------------------------------------------------------------
# LLM (Ollama)
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL: str = _get_env("JARVIS_OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL: str = _get_env("JARVIS_OLLAMA_MODEL", "qwen2.5-coder:7b")
OLLAMA_TIMEOUT: int = _get_env_int("JARVIS_OLLAMA_TIMEOUT", 120)

LLM_SYSTEM_PROMPT: str = _get_env(
    "JARVIS_SYSTEM_PROMPT",
    (
        "You are Jarvis, a helpful local AI assistant. "
        "Be concise and direct. "
        "You run entirely on this machine — no cloud, no cost, full privacy."
    ),
)
LLM_MAX_HISTORY: int = _get_env_int("JARVIS_MAX_HISTORY", 10)
"""Maximum number of user/assistant exchange pairs to keep in context."""

# ---------------------------------------------------------------------------
# Text-to-Speech
# ---------------------------------------------------------------------------
PIPER_VOICE: str = _get_env("JARVIS_PIPER_VOICE", "en_US-lessac-medium")
MACOS_SAY_VOICE: str = _get_env("JARVIS_SAY_VOICE", "Daniel")
"""Default macOS say voice.  Daniel is a high-quality male voice."""

TTS_BACKEND: str = _get_env("JARVIS_TTS_BACKEND", "auto")
"""TTS backend: 'piper', 'say', or 'auto' (try piper, fall back to say)."""

# ---------------------------------------------------------------------------
# Activation sound
# ---------------------------------------------------------------------------
BLIP_FREQUENCY_HZ: int = _get_env_int("JARVIS_BLIP_FREQ", 880)
BLIP_DURATION_MS: int = _get_env_int("JARVIS_BLIP_DURATION_MS", 150)

# ---------------------------------------------------------------------------
# Conversation Memory
# ---------------------------------------------------------------------------
HISTORY_FILE: str = _get_env("JARVIS_HISTORY_FILE", "~/.jarvis/history.json")
"""Path to the persistent conversation history JSON file."""

HISTORY_MAX_ENTRIES: int = _get_env_int("JARVIS_HISTORY_MAX_ENTRIES", 200)
"""Maximum number of message entries to keep in the history file."""

# ---------------------------------------------------------------------------
# Health / Status Server
# ---------------------------------------------------------------------------
HEALTH_PORT: int = _get_env_int("JARVIS_HEALTH_PORT", 8765)
"""TCP port for the /health and /status HTTP endpoints."""

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL: str = _get_env("JARVIS_LOG_LEVEL", "INFO")
