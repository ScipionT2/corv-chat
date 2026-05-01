"""
Configuration module for EP Agent (formerly Jarvis Voice Bridge).

All settings are configurable via environment variables or a .env file.
Sensible defaults are provided for a zero-config startup experience.

ENV prefix: EP_* (with JARVIS_* fallback for backward compatibility).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root if it exists
_PROJECT_ROOT = Path(__file__).parent
_ENV_PATH = _PROJECT_ROOT / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)


def _get_env(key: str, default: str, legacy_key: str | None = None) -> str:
    """Get an environment variable with optional legacy fallback."""
    val = os.environ.get(key)
    if val is not None:
        return val
    if legacy_key:
        val = os.environ.get(legacy_key)
        if val is not None:
            return val
    return default


def _get_env_int(key: str, default: int, legacy_key: str | None = None) -> int:
    """Get an integer environment variable with optional legacy fallback."""
    raw = os.environ.get(key)
    if raw is None and legacy_key:
        raw = os.environ.get(legacy_key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_env_float(key: str, default: float, legacy_key: str | None = None) -> float:
    """Get a float environment variable with optional legacy fallback."""
    raw = os.environ.get(key)
    if raw is None and legacy_key:
        raw = os.environ.get(legacy_key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Wake word
# ---------------------------------------------------------------------------
WAKE_WORD: str = _get_env("EP_WAKE_WORD", "jarvis", "JARVIS_WAKE_WORD")
WAKE_WORD_CONFIDENCE: float = _get_env_float("EP_WAKE_CONFIDENCE", 0.5, "JARVIS_WAKE_CONFIDENCE")

# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
SAMPLE_RATE: int = _get_env_int("EP_SAMPLE_RATE", 16000, "JARVIS_SAMPLE_RATE")
CHANNELS: int = 1
AUDIO_CHUNK_SAMPLES: int = _get_env_int("EP_CHUNK_SAMPLES", 1280, "JARVIS_CHUNK_SAMPLES")
"""Number of samples per audio chunk (1280 ≈ 80 ms at 16 kHz)."""

# ---------------------------------------------------------------------------
# Recorder / VAD
# ---------------------------------------------------------------------------
SILENCE_THRESHOLD_MS: int = _get_env_int("EP_SILENCE_MS", 800, "JARVIS_SILENCE_MS")
"""Milliseconds of silence before recording stops."""

SILENCE_ENERGY_THRESHOLD: float = _get_env_float("EP_SILENCE_ENERGY", 0.008, "JARVIS_SILENCE_ENERGY")
"""RMS energy below this value is considered silence."""

MAX_RECORD_SECONDS: int = _get_env_int("EP_MAX_RECORD_SEC", 30, "JARVIS_MAX_RECORD_SEC")
"""Safety cutoff for a single recording."""

# ---------------------------------------------------------------------------
# Speech-to-Text (faster-whisper)
# ---------------------------------------------------------------------------
WHISPER_MODEL: str = _get_env("EP_WHISPER_MODEL", "base.en", "JARVIS_WHISPER_MODEL")
WHISPER_DEVICE: str = _get_env("EP_WHISPER_DEVICE", "auto", "JARVIS_WHISPER_DEVICE")
"""Device for Whisper inference: 'auto' picks Metal/CoreML on Apple Silicon, CUDA on NVIDIA."""
WHISPER_COMPUTE_TYPE: str = _get_env("EP_WHISPER_COMPUTE", "int8", "JARVIS_WHISPER_COMPUTE")

# ---------------------------------------------------------------------------
# LLM (Ollama — runs 100% offline)
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL: str = _get_env("EP_OLLAMA_URL", "http://localhost:11434", "JARVIS_OLLAMA_URL")
OLLAMA_MODEL: str = _get_env("EP_OLLAMA_MODEL", "qwen2.5:3b", "JARVIS_OLLAMA_MODEL")
"""Default model. Quantized 3B is ~2GB VRAM and fast on Metal. Override with 7B if GPU headroom allows."""
OLLAMA_TIMEOUT: int = _get_env_int("EP_OLLAMA_TIMEOUT", 120, "JARVIS_OLLAMA_TIMEOUT")

# GPU / Metal Acceleration — Ollama handles this via model format (GGUF Q4_K_M)
OLLAMA_NUM_GPU: int = _get_env_int("OLLAMA_NUM_GPU", -1)
"""Number of GPU layers to offload. -1 = all layers (full GPU). 0 = CPU only."""

# ---------------------------------------------------------------------------
# Offline mode — entire stack works without internet
# ---------------------------------------------------------------------------
OFFLINE_MODE: bool = _get_env("EP_OFFLINE", "false", "JARVIS_OFFLINE").lower() in ("true", "1", "yes")
"""When True, skip ALL network calls (HuggingFace model checks, etc). Models must be pre-cached."""

# ---------------------------------------------------------------------------
# Hybrid Mode — auto-switch between cloud and local
# ---------------------------------------------------------------------------
HYBRID_MODE: bool = _get_env("EP_HYBRID", "true").lower() in ("true", "1", "yes")
"""When True, use the hybrid LLM client (cloud + local fallback). Requires OPENAI_API_KEY."""

OPENAI_MODEL: str = _get_env("EP_OPENAI_MODEL", "gpt-4o")
"""Cloud model for high-reasoning tasks when online."""

PING_THRESHOLD_MS: int = _get_env_int("EP_PING_THRESHOLD_MS", 500)
"""If ping to cloud exceeds this (ms), switch to local mode."""

LLM_SYSTEM_PROMPT: str = _get_env(
    "EP_SYSTEM_PROMPT",
    (
        "You are EP Agent, a personal AI assistant with full system access. "
        "You can see and analyze the user's screen, open/close your side panel, "
        "and control applications. Be concise and direct. "
        "You have a visual side panel on the right side of the screen. "
        "When asked about the screen, you analyze it with your vision system. "
        "You run in hybrid mode: cloud when connected, local when offline."
    ),
    "JARVIS_SYSTEM_PROMPT",
)
LLM_MAX_HISTORY: int = _get_env_int("EP_MAX_HISTORY", 10, "JARVIS_MAX_HISTORY")
"""Maximum number of user/assistant exchange pairs to keep in context."""

# ---------------------------------------------------------------------------
# Text-to-Speech
# ---------------------------------------------------------------------------
PIPER_VOICE: str = _get_env("EP_PIPER_VOICE", "en_US-lessac-medium", "JARVIS_PIPER_VOICE")
MACOS_SAY_VOICE: str = _get_env("EP_SAY_VOICE", "Daniel", "JARVIS_SAY_VOICE")
"""Default macOS say voice. Daniel is a high-quality male voice."""

TTS_BACKEND: str = _get_env("EP_TTS_BACKEND", "auto", "JARVIS_TTS_BACKEND")
"""TTS backend: 'piper', 'say', or 'auto' (try piper, fall back to say)."""

# ---------------------------------------------------------------------------
# Activation sound
# ---------------------------------------------------------------------------
BLIP_FREQUENCY_HZ: int = _get_env_int("EP_BLIP_FREQ", 880, "JARVIS_BLIP_FREQ")
BLIP_DURATION_MS: int = _get_env_int("EP_BLIP_DURATION_MS", 150, "JARVIS_BLIP_DURATION_MS")

# New calm chime settings
CHIME_FREQUENCY_HZ: int = _get_env_int("EP_CHIME_FREQ", 480)
CHIME_DURATION_MS: int = _get_env_int("EP_CHIME_DURATION_MS", 200)

# ---------------------------------------------------------------------------
# Accent color (from profile or default)
# ---------------------------------------------------------------------------
ACCENT_COLOR: str = _get_env("EP_ACCENT_COLOR", "cyan")

# ---------------------------------------------------------------------------
# Conversation Memory
# ---------------------------------------------------------------------------
HISTORY_FILE: str = _get_env("EP_HISTORY_FILE", "~/.ep-agent/history.json", "JARVIS_HISTORY_FILE")
"""Path to the persistent conversation history JSON file."""

HISTORY_MAX_ENTRIES: int = _get_env_int("EP_HISTORY_MAX_ENTRIES", 200, "JARVIS_HISTORY_MAX_ENTRIES")
"""Maximum number of message entries to keep in the history file."""

# ---------------------------------------------------------------------------
# Health / Status Server
# ---------------------------------------------------------------------------
HEALTH_PORT: int = _get_env_int("EP_HEALTH_PORT", 8765, "JARVIS_HEALTH_PORT")
"""TCP port for the /health and /status HTTP endpoints."""

# ---------------------------------------------------------------------------
# Vision (Screen Analysis) — DISABLED by default
# ---------------------------------------------------------------------------
VISION_ENABLED: bool = _get_env("EP_VISION", "false", "JARVIS_VISION").lower() in ("true", "1", "yes")
"""Vision is opt-in. Enable with EP_VISION=true or --vision flag."""

VISION_MODEL: str = _get_env("EP_VISION_MODEL", "moondream:1.8b", "JARVIS_VISION_MODEL")
"""Ollama vision model for screen analysis. moondream 1.8B is 10x lighter than llama3.2-vision."""

VISION_INTERVAL: float = _get_env_float("EP_VISION_INTERVAL", 10.0, "JARVIS_VISION_INTERVAL")
"""Seconds between screen change checks (lightweight hash compare, not model inference)."""

VISION_MAX_TOKENS: int = _get_env_int("EP_VISION_MAX_TOKENS", 512, "JARVIS_VISION_MAX_TOKENS")
"""Max tokens for vision model response."""

VISION_SCALE: float = _get_env_float("EP_VISION_SCALE", 0.5, "JARVIS_VISION_SCALE")
"""Screenshot downscale factor (0.5 = half res, faster inference)."""

VISION_MONITOR: int = _get_env_int("EP_VISION_MONITOR", 0, "JARVIS_VISION_MONITOR")
"""Monitor index for screen capture (0 = all, 1 = primary)."""

VISION_CHANGE_THRESHOLD: float = _get_env_float("EP_VISION_CHANGE_THRESHOLD", 0.15)
"""Minimum pixel-change ratio (0.0-1.0) to trigger model inference. 0.15 = 15% change."""

VISION_SLEEP_TIMEOUT: float = _get_env_float("EP_VISION_SLEEP_TIMEOUT", 30.0)
"""Seconds of no screen change before vision enters deep sleep."""

VISION_PROMPT: str = _get_env(
    "EP_VISION_PROMPT",
    (
        "Analyze this screenshot. Extract the key information visible on screen "
        "and suggest logical next steps. Be concise."
    ),
    "JARVIS_VISION_PROMPT",
)

# ---------------------------------------------------------------------------
# Sidebar UI (always-on side panel)
# ---------------------------------------------------------------------------
SIDEBAR_ENABLED: bool = _get_env("EP_SIDEBAR", "true").lower() in ("true", "1", "yes")
"""Enable the always-on sidebar. Cmd+Shift+E to toggle visibility."""

# Legacy overlay settings (kept for backward compat)
OVERLAY_WIDTH: int = _get_env_int("EP_OVERLAY_WIDTH", 380, "JARVIS_OVERLAY_WIDTH")
OVERLAY_OPACITY: float = _get_env_float("EP_OVERLAY_OPACITY", 0.92, "JARVIS_OVERLAY_OPACITY")
OVERLAY_ENABLED: bool = _get_env("EP_OVERLAY", "false", "JARVIS_OVERLAY").lower() in ("true", "1", "yes")
"""Legacy overlay toggle. Sidebar replaces this."""

# ---------------------------------------------------------------------------
# Menu Bar (macOS system tray)
# ---------------------------------------------------------------------------
MENUBAR_ENABLED: bool = _get_env("EP_MENUBAR", "true").lower() in ("true", "1", "yes")
"""Enable the macOS menu bar extra. Lightweight status + controls."""

# ---------------------------------------------------------------------------
# Process Priority / Resource Management
# ---------------------------------------------------------------------------
PROCESS_PRIORITY: str = _get_env("EP_PROCESS_PRIORITY", "low", "JARVIS_PROCESS_PRIORITY")
"""Process priority: 'low', 'normal', 'high'. Low prevents OS freezing."""

MAX_CPU_PERCENT: float = _get_env_float("EP_MAX_CPU_PERCENT", 25.0, "JARVIS_MAX_CPU_PERCENT")
"""Soft CPU cap (lowered from 50% to 25% for background operation)."""

KV_CACHE_FLUSH_INTERVAL: int = _get_env_int("EP_KV_FLUSH_MINUTES", 10)
"""Minutes between KV cache flushes (clears Ollama's VRAM context cache)."""

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL: str = _get_env("EP_LOG_LEVEL", "INFO", "JARVIS_LOG_LEVEL")

# ---------------------------------------------------------------------------
# Profile / Onboarding
# ---------------------------------------------------------------------------
PROFILE_PATH: str = os.path.expanduser("~/.ep-agent/profile.json")
