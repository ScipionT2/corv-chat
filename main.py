#!/usr/bin/env python3
"""
EP Agent Voice Bridge — Local AI Voice Assistant.

Entry point that configures logging, parses CLI arguments, and starts the
voice-interaction pipeline. Ctrl+C shuts everything down gracefully.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

import config
from src.pipeline import EPAgentPipeline

BANNER = r"""
╔══════════════════════════════════════════════════╗
║  ⚡ EP Agent — Local AI Voice Assistant          ║
║  100%% local · zero cost · full privacy           ║
╚══════════════════════════════════════════════════╝
"""


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="EP Agent — fully local voice assistant",
    )
    parser.add_argument(
        "--model",
        default=config.OLLAMA_MODEL,
        help=f"Ollama model to use (default: {config.OLLAMA_MODEL})",
    )
    parser.add_argument(
        "--wake-word",
        default=config.WAKE_WORD,
        help=f"Wake word (default: {config.WAKE_WORD})",
    )
    parser.add_argument(
        "--whisper-model",
        default=config.WHISPER_MODEL,
        help=f"Whisper model size (default: {config.WHISPER_MODEL})",
    )
    parser.add_argument(
        "--tts",
        default=config.TTS_BACKEND,
        choices=["auto", "piper", "say"],
        help=f"TTS backend (default: {config.TTS_BACKEND})",
    )
    parser.add_argument(
        "--voice",
        default=None,
        help=f"macOS say voice name (default: {config.MACOS_SAY_VOICE}). "
             "Use --list-voices to see available options.",
    )
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="List available macOS TTS voices and exit.",
    )
    parser.add_argument(
        "--vision",
        action="store_true",
        default=config.VISION_ENABLED,
        help="Enable vision/screen analysis (disabled by default for speed)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        default=config.OFFLINE_MODE,
        help="Force offline mode (skip all network calls)",
    )
    parser.add_argument(
        "--log-level",
        default=config.LOG_LEVEL,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help=f"Logging level (default: {config.LOG_LEVEL})",
    )
    return parser.parse_args()


def _setup_logging(level: str) -> None:
    """Configure root logger."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    """Application entry point."""
    args = _parse_args()
    _setup_logging(args.log_level)

    # ── Resource optimization (apply BEFORE heavy imports) ──────────
    from src.resource_manager import (
        limit_cpu_threads,
        set_process_priority,
        set_ollama_gpu_layers,
        log_system_info,
    )
    limit_cpu_threads()         # 25% CPU thread cap
    set_process_priority()      # Low priority → never freezes the OS
    set_ollama_gpu_layers()     # Force Metal/CUDA offload for Ollama
    log_system_info()           # Log hardware capabilities

    # --list-voices: print and exit
    if args.list_voices:
        from src.tts import get_available_voices

        voices = get_available_voices()
        if not voices:
            print("No macOS voices available (are you on macOS?)")
            sys.exit(1)
        print(f"{'Name':<20} {'Language':<12} Description")
        print("-" * 60)
        for v in voices:
            print(f"{v['name']:<20} {v['language']:<12} {v.get('description', '')}")
        sys.exit(0)

    voice = args.voice or config.MACOS_SAY_VOICE

    print(BANNER)
    print(f"  Wake word   : {args.wake_word}")
    print(f"  LLM model   : {args.model}")
    print(f"  Whisper      : {args.whisper_model}")
    print(f"  TTS backend  : {args.tts}")
    print(f"  Voice        : {voice}")
    print(f"  Ollama URL   : {config.OLLAMA_BASE_URL}")
    print()

    pipeline = EPAgentPipeline(
        wake_word=args.wake_word,
        ollama_model=args.model,
        whisper_model=args.whisper_model,
        tts_backend=args.tts,
        voice=args.voice,
    )

    # Graceful shutdown on Ctrl+C / SIGTERM
    def _shutdown(signum: int, frame) -> None:
        print("\n⏹  Shutting down …")
        pipeline.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        pipeline.start()
        pipeline.wait()
    except KeyboardInterrupt:
        print("\n⏹  Interrupted")
        pipeline.stop()
    except Exception as exc:
        logging.getLogger(__name__).critical("Fatal error: %s", exc, exc_info=True)
        pipeline.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
