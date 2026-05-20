"""
Configuration module for Nova (formerly EP Agent / Jarvis Voice Bridge).

All settings are configurable via environment variables or a .env file.
Sensible defaults are provided for a zero-config startup experience.

ENV prefix: NOVA_* (with EP_* and JARVIS_* fallback for backward compatibility).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root if it exists
_PROJECT_ROOT = Path(__file__).parent
_ENV_PATH = _PROJECT_ROOT / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)


def _get_env(key: str, default: str, legacy_key: str | None = None, legacy_key2: str | None = None) -> str:
    """Get an environment variable with optional legacy fallbacks."""
    val = os.environ.get(key)
    if val is not None:
        return val
    if legacy_key:
        val = os.environ.get(legacy_key)
        if val is not None:
            return val
    if legacy_key2:
        val = os.environ.get(legacy_key2)
        if val is not None:
            return val
    return default


def _get_env_int(key: str, default: int, legacy_key: str | None = None, legacy_key2: str | None = None) -> int:
    """Get an integer environment variable with optional legacy fallbacks."""
    raw = os.environ.get(key)
    if raw is None and legacy_key:
        raw = os.environ.get(legacy_key)
    if raw is None and legacy_key2:
        raw = os.environ.get(legacy_key2)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_env_float(key: str, default: float, legacy_key: str | None = None, legacy_key2: str | None = None) -> float:
    """Get a float environment variable with optional legacy fallbacks."""
    raw = os.environ.get(key)
    if raw is None and legacy_key:
        raw = os.environ.get(legacy_key)
    if raw is None and legacy_key2:
        raw = os.environ.get(legacy_key2)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Wake word
# ---------------------------------------------------------------------------
WAKE_WORD: str = _get_env("NOVA_WAKE_WORD", "nova", "EP_WAKE_WORD", "JARVIS_WAKE_WORD")
WAKE_WORD_CONFIDENCE: float = _get_env_float("NOVA_WAKE_CONFIDENCE", 0.5, "EP_WAKE_CONFIDENCE", "JARVIS_WAKE_CONFIDENCE")

WAKE_WORD_BACKEND: str = _get_env("NOVA_WAKE_BACKEND", "auto")
"""Wake word backend: 'auto', 'keyword', or 'openwakeword'.
'auto' uses keyword detection for 'nova' and openwakeword for built-in words like 'jarvis'.
'keyword' forces faster-whisper keyword spotting.
'openwakeword' forces the OpenWakeWord model."""

WAKE_KEYWORD_BUFFER_SEC: float = _get_env_float("NOVA_WAKE_KEYWORD_BUFFER", 1.5)
"""Seconds of audio to buffer before running keyword STT detection."""

WAKE_KEYWORD_ENERGY_THRESHOLD: float = _get_env_float("NOVA_WAKE_KEYWORD_ENERGY", 0.01)
"""RMS energy threshold — only transcribe when someone is speaking."""

WAKE_KEYWORD_WHISPER_MODEL: str = _get_env("NOVA_WAKE_KEYWORD_WHISPER", "tiny.en")
"""Whisper model for keyword detection. 'tiny.en' for lowest latency."""

# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
SAMPLE_RATE: int = _get_env_int("NOVA_SAMPLE_RATE", 16000, "EP_SAMPLE_RATE", "JARVIS_SAMPLE_RATE")
CHANNELS: int = 1
AUDIO_CHUNK_SAMPLES: int = _get_env_int("NOVA_CHUNK_SAMPLES", 1280, "EP_CHUNK_SAMPLES", "JARVIS_CHUNK_SAMPLES")
"""Number of samples per audio chunk (1280 ≈ 80 ms at 16 kHz)."""

# ---------------------------------------------------------------------------
# Recorder / VAD
# ---------------------------------------------------------------------------
SILENCE_THRESHOLD_MS: int = _get_env_int("NOVA_SILENCE_MS", 800, "EP_SILENCE_MS", "JARVIS_SILENCE_MS")
"""Milliseconds of silence before recording stops."""

SILENCE_ENERGY_THRESHOLD: float = _get_env_float("NOVA_SILENCE_ENERGY", 0.008, "EP_SILENCE_ENERGY", "JARVIS_SILENCE_ENERGY")
"""RMS energy below this value is considered silence."""

MAX_RECORD_SECONDS: int = _get_env_int("NOVA_MAX_RECORD_SEC", 30, "EP_MAX_RECORD_SEC", "JARVIS_MAX_RECORD_SEC")
"""Safety cutoff for a single recording."""

# ---------------------------------------------------------------------------
# Speech-to-Text (faster-whisper)
# ---------------------------------------------------------------------------
WHISPER_MODEL: str = _get_env("NOVA_WHISPER_MODEL", "base.en", "EP_WHISPER_MODEL", "JARVIS_WHISPER_MODEL")
WHISPER_DEVICE: str = _get_env("NOVA_WHISPER_DEVICE", "auto", "EP_WHISPER_DEVICE", "JARVIS_WHISPER_DEVICE")
"""Device for Whisper inference: 'auto' picks Metal/CoreML on Apple Silicon, CUDA on NVIDIA."""
WHISPER_COMPUTE_TYPE: str = _get_env("NOVA_WHISPER_COMPUTE", "int8", "EP_WHISPER_COMPUTE", "JARVIS_WHISPER_COMPUTE")

# ---------------------------------------------------------------------------
# LLM (Ollama — runs 100% offline)
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL: str = _get_env("NOVA_OLLAMA_URL", "http://localhost:11434", "EP_OLLAMA_URL", "JARVIS_OLLAMA_URL")
OLLAMA_MODEL: str = _get_env("NOVA_OLLAMA_MODEL", "qwen2.5:3b", "EP_OLLAMA_MODEL", "JARVIS_OLLAMA_MODEL")
"""Default model. Quantized 3B is ~2GB VRAM and fast on Metal. Override with 7B if GPU headroom allows."""
OLLAMA_TIMEOUT: int = _get_env_int("NOVA_OLLAMA_TIMEOUT", 30, "EP_OLLAMA_TIMEOUT", "JARVIS_OLLAMA_TIMEOUT")

NOVA_RESPONSE_TIMEOUT: int = _get_env_int("NOVA_RESPONSE_TIMEOUT", 30, "EP_RESPONSE_TIMEOUT")
"""Overall response timeout in seconds. Used as default for Ollama and other LLM backends."""
OLLAMA_NUM_CTX: int = _get_env_int("NOVA_NUM_CTX", 2048, "EP_NUM_CTX")
"""Context window size. Smaller = faster inference."""

# GPU / Metal Acceleration — Ollama handles this via model format (GGUF Q4_K_M)
OLLAMA_NUM_GPU: int = _get_env_int("OLLAMA_NUM_GPU", -1)
"""Number of GPU layers to offload. -1 = all layers (full GPU). 0 = CPU only."""

# ---------------------------------------------------------------------------
# Offline mode — entire stack works without internet
# ---------------------------------------------------------------------------
OFFLINE_MODE: bool = _get_env("NOVA_OFFLINE", "false", "EP_OFFLINE", "JARVIS_OFFLINE").lower() in ("true", "1", "yes")
"""When True, skip ALL network calls (HuggingFace model checks, etc). Models must be pre-cached."""

# ---------------------------------------------------------------------------
# Hybrid Mode — auto-switch between cloud and local
# ---------------------------------------------------------------------------
HYBRID_MODE: bool = _get_env("NOVA_HYBRID", "true", "EP_HYBRID").lower() in ("true", "1", "yes")
"""When True, use the hybrid LLM client (cloud primary, local fallback).
Default is online — uses cloud when connected, auto-falls back to local when offline.
Set EP_HYBRID=false to force offline-only."""

OPENAI_MODEL: str = _get_env("NOVA_OPENAI_MODEL", "gpt-4o", "EP_OPENAI_MODEL")
"""Cloud model for high-reasoning tasks when online."""

PING_THRESHOLD_MS: int = _get_env_int("NOVA_PING_THRESHOLD_MS", 500, "EP_PING_THRESHOLD_MS")
"""If ping to cloud exceeds this (ms), switch to local mode."""

LLM_SYSTEM_PROMPT: str = _get_env(
    "NOVA_SYSTEM_PROMPT",
    (
        "You are Nova, a personal AI assistant with full system access. "
        "You can see and analyze the user's screen, open/close your side panel, "
        "and control applications. Be concise and direct. "
        "You have a visual side panel on the right side of the screen. "
        "When asked about the screen, you analyze it with your vision system. "
        "You run online by default (cloud LLM when connected, local fallback when offline)."
    ),
    "EP_SYSTEM_PROMPT",
    "JARVIS_SYSTEM_PROMPT",
)
LLM_MAX_HISTORY: int = _get_env_int("NOVA_MAX_HISTORY", 6, "EP_MAX_HISTORY", "JARVIS_MAX_HISTORY")
"""Maximum number of user/assistant exchange pairs to keep in context."""

# ---------------------------------------------------------------------------
# Text-to-Speech
# ---------------------------------------------------------------------------
PIPER_VOICE: str = _get_env("NOVA_PIPER_VOICE", "en_US-lessac-medium", "EP_PIPER_VOICE", "JARVIS_PIPER_VOICE")
MACOS_SAY_VOICE: str = _get_env("NOVA_SAY_VOICE", "Daniel", "EP_SAY_VOICE", "JARVIS_SAY_VOICE")
"""Default macOS say voice. Daniel is a high-quality male voice."""

TTS_BACKEND: str = _get_env("NOVA_TTS_BACKEND", "auto", "EP_TTS_BACKEND", "JARVIS_TTS_BACKEND")
"""TTS backend: 'piper', 'say', or 'auto' (try piper, fall back to say)."""

# ---------------------------------------------------------------------------
# Activation sound
# ---------------------------------------------------------------------------
BLIP_FREQUENCY_HZ: int = _get_env_int("NOVA_BLIP_FREQ", 880, "EP_BLIP_FREQ", "JARVIS_BLIP_FREQ")
BLIP_DURATION_MS: int = _get_env_int("NOVA_BLIP_DURATION_MS", 150, "EP_BLIP_DURATION_MS", "JARVIS_BLIP_DURATION_MS")

# New calm chime settings
CHIME_FREQUENCY_HZ: int = _get_env_int("NOVA_CHIME_FREQ", 480, "EP_CHIME_FREQ")
CHIME_DURATION_MS: int = _get_env_int("NOVA_CHIME_DURATION_MS", 200, "EP_CHIME_DURATION_MS")

# ---------------------------------------------------------------------------
# Accent color (from profile or default)
# ---------------------------------------------------------------------------
ACCENT_COLOR: str = _get_env("NOVA_ACCENT_COLOR", "cyan", "EP_ACCENT_COLOR")

# ---------------------------------------------------------------------------
# Conversation Memory
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# OpenRouter (multi-model memory agents)
# ---------------------------------------------------------------------------
OPENROUTER_API_KEY: str = _get_env("NOVA_OPENROUTER_API_KEY", "", "OPENROUTER_API_KEY")
OPENROUTER_BASE_URL: str = _get_env("NOVA_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
ARCHITECT_MODEL: str = _get_env("NOVA_ARCHITECT_MODEL", "anthropic/claude-3.5-sonnet")
CONTEXT_PROCESSOR_MODEL: str = _get_env("NOVA_CONTEXT_PROCESSOR_MODEL", "google/gemini-flash-1.5")
EXECUTION_MODEL: str = _get_env("NOVA_EXECUTION_MODEL", "openai/gpt-4o-mini")

# ---------------------------------------------------------------------------
# 3-Tier Memory System
# ---------------------------------------------------------------------------
MEMORY_DIR: str = _get_env("NOVA_MEMORY_DIR", "~/.nova/memory/")
MEMORY_MAX_BLOCKS: int = _get_env_int("NOVA_MEMORY_MAX_BLOCKS", 100)
MEMORY_RELEVANCE_TOP_K: int = _get_env_int("NOVA_MEMORY_RELEVANCE_TOP_K", 5)

# ---------------------------------------------------------------------------
# Conversation Memory (legacy)
# ---------------------------------------------------------------------------
HISTORY_FILE: str = _get_env("NOVA_HISTORY_FILE", "~/.nova/history.json", "EP_HISTORY_FILE", "JARVIS_HISTORY_FILE")
"""Path to the persistent conversation history JSON file."""

HISTORY_MAX_ENTRIES: int = _get_env_int("NOVA_HISTORY_MAX_ENTRIES", 200, "EP_HISTORY_MAX_ENTRIES", "JARVIS_HISTORY_MAX_ENTRIES")
"""Maximum number of message entries to keep in the history file."""

# ---------------------------------------------------------------------------
# Health / Status Server
# ---------------------------------------------------------------------------
HEALTH_PORT: int = _get_env_int("NOVA_HEALTH_PORT", 8765, "EP_HEALTH_PORT", "JARVIS_HEALTH_PORT")
"""TCP port for the /health and /status HTTP endpoints."""

# ---------------------------------------------------------------------------
# Vision (Screen Analysis) — DISABLED by default
# ---------------------------------------------------------------------------
VISION_ENABLED: bool = _get_env("NOVA_VISION", "true", "EP_VISION", "JARVIS_VISION").lower() in ("true", "1", "yes")
"""Vision is enabled by default. Disable with NOVA_VISION=false."""

VISION_MODEL: str = _get_env("NOVA_VISION_MODEL", "moondream:1.8b", "EP_VISION_MODEL", "JARVIS_VISION_MODEL")
"""Ollama vision model for screen analysis. moondream 1.8B is 10x lighter than llama3.2-vision."""

VISION_INTERVAL: float = _get_env_float("NOVA_VISION_INTERVAL", 10.0, "EP_VISION_INTERVAL", "JARVIS_VISION_INTERVAL")
"""Seconds between screen change checks (lightweight hash compare, not model inference)."""

VISION_MAX_TOKENS: int = _get_env_int("NOVA_VISION_MAX_TOKENS", 512, "EP_VISION_MAX_TOKENS", "JARVIS_VISION_MAX_TOKENS")
"""Max tokens for vision model response."""

VISION_SCALE: float = _get_env_float("NOVA_VISION_SCALE", 0.5, "EP_VISION_SCALE", "JARVIS_VISION_SCALE")
"""Screenshot downscale factor (0.5 = half res, faster inference)."""

VISION_MONITOR: int = _get_env_int("NOVA_VISION_MONITOR", 0, "EP_VISION_MONITOR", "JARVIS_VISION_MONITOR")
"""Monitor index for screen capture (0 = all, 1 = primary)."""

VISION_CHANGE_THRESHOLD: float = _get_env_float("NOVA_VISION_CHANGE_THRESHOLD", 0.15, "EP_VISION_CHANGE_THRESHOLD")
"""Minimum pixel-change ratio (0.0-1.0) to trigger model inference. 0.15 = 15% change."""

VISION_SLEEP_TIMEOUT: float = _get_env_float("NOVA_VISION_SLEEP_TIMEOUT", 30.0, "EP_VISION_SLEEP_TIMEOUT")
"""Seconds of no screen change before vision enters deep sleep."""

VISION_FAST_MODE: bool = _get_env("NOVA_VISION_FAST_MODE", "false", "EP_VISION_FAST_MODE").lower() in ("true", "1", "yes")
"""Fast mode: lower resolution (0.3x) and fewer tokens (256) for faster vision responses."""

VISION_PROMPT: str = _get_env(
    "NOVA_VISION_PROMPT",
    (
        "Analyze this screenshot. Extract the key information visible on screen "
        "and suggest logical next steps. Be concise."
    ),
    "EP_VISION_PROMPT",
    "JARVIS_VISION_PROMPT",
)

VISION_WINDOW_ONLY: bool = _get_env("NOVA_VISION_WINDOW_ONLY", "true", "EP_VISION_WINDOW_ONLY").lower() in ("true", "1", "yes")
"""When True, capture only the active window instead of the full screen (more focused analysis)."""

VISION_HISTORY_SIZE: int = _get_env_int("NOVA_VISION_HISTORY_SIZE", 20, "EP_VISION_HISTORY_SIZE")
"""Maximum number of vision analysis entries to keep in history."""

# ---------------------------------------------------------------------------
# Sidebar UI (always-on side panel)
# ---------------------------------------------------------------------------
SIDEBAR_ENABLED: bool = _get_env("NOVA_SIDEBAR", "true", "EP_SIDEBAR").lower() in ("true", "1", "yes")
"""Enable the always-on sidebar. Cmd+Shift+E to toggle visibility."""

# Legacy overlay settings (kept for backward compat)
OVERLAY_WIDTH: int = _get_env_int("NOVA_OVERLAY_WIDTH", 380, "EP_OVERLAY_WIDTH", "JARVIS_OVERLAY_WIDTH")
OVERLAY_OPACITY: float = _get_env_float("NOVA_OVERLAY_OPACITY", 0.92, "EP_OVERLAY_OPACITY", "JARVIS_OVERLAY_OPACITY")
OVERLAY_ENABLED: bool = _get_env("NOVA_OVERLAY", "false", "EP_OVERLAY", "JARVIS_OVERLAY").lower() in ("true", "1", "yes")
"""Legacy overlay toggle. Sidebar replaces this."""

# ---------------------------------------------------------------------------
# Menu Bar (macOS system tray)
# ---------------------------------------------------------------------------
MENUBAR_ENABLED: bool = _get_env("NOVA_MENUBAR", "true", "EP_MENUBAR").lower() in ("true", "1", "yes")
"""Enable the macOS menu bar extra. Lightweight status + controls."""

# ---------------------------------------------------------------------------
# Process Priority / Resource Management
# ---------------------------------------------------------------------------
PROCESS_PRIORITY: str = _get_env("NOVA_PROCESS_PRIORITY", "low", "EP_PROCESS_PRIORITY", "JARVIS_PROCESS_PRIORITY")
"""Process priority: 'low', 'normal', 'high'. Low prevents OS freezing."""

MAX_CPU_PERCENT: float = _get_env_float("NOVA_MAX_CPU_PERCENT", 25.0, "EP_MAX_CPU_PERCENT", "JARVIS_MAX_CPU_PERCENT")
"""Soft CPU cap (lowered from 50% to 25% for background operation)."""

KV_CACHE_FLUSH_INTERVAL: int = _get_env_int("NOVA_KV_FLUSH_MINUTES", 10, "EP_KV_FLUSH_MINUTES")
"""Minutes between KV cache flushes (clears Ollama's VRAM context cache)."""

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL: str = _get_env("NOVA_LOG_LEVEL", "INFO", "EP_LOG_LEVEL", "JARVIS_LOG_LEVEL")

# ---------------------------------------------------------------------------
# Profile / Onboarding
# ---------------------------------------------------------------------------
PROFILE_PATH: str = os.path.expanduser("~/.nova/profile.json")

# ---------------------------------------------------------------------------
# Watchdog / Crash Recovery
# ---------------------------------------------------------------------------
WATCHDOG_ENABLED: bool = _get_env("NOVA_WATCHDOG", "true").lower() in ("true", "1", "yes")
"""Enable the pipeline watchdog thread for auto-restart on component failure."""

WATCHDOG_INTERVAL: int = _get_env_int("NOVA_WATCHDOG_INTERVAL", 5)
"""Seconds between watchdog health checks."""

MAX_RESTART_ATTEMPTS: int = _get_env_int("NOVA_MAX_RESTARTS", 5)
"""Maximum component restart attempts within the cooldown window."""

RESTART_COOLDOWN: int = _get_env_int("NOVA_RESTART_COOLDOWN", 30)
"""Seconds cooldown window for restart attempt tracking."""
