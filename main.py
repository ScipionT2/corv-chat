#!/usr/bin/env python3
"""
Jarvis Voice Bridge — Local AI Voice Assistant.

Entry point that configures logging, parses CLI arguments, and starts the
voice-interaction pipeline.  Ctrl+C shuts everything down gracefully.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

import config
from src.pipeline import JarvisPipeline

BANNER = r"""
╔══════════════════════════════════════════════════╗
║  🤖 Jarvis Voice Bridge — Local AI Assistant     ║
║  100%% local · zero cost · full privacy           ║
╚══════════════════════════════════════════════════╝
"""


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Jarvis Voice Bridge — fully local voice assistant",
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

    print(BANNER)
    print(f"  Wake word   : {args.wake_word}")
    print(f"  LLM model   : {args.model}")
    print(f"  Whisper      : {args.whisper_model}")
    print(f"  TTS backend  : {args.tts}")
    print(f"  Ollama URL   : {config.OLLAMA_BASE_URL}")
    print()

    pipeline = JarvisPipeline(
        wake_word=args.wake_word,
        ollama_model=args.model,
        whisper_model=args.whisper_model,
        tts_backend=args.tts,
    )

    # Graceful shutdown on Ctrl+C / SIGTERM
    def _shutdown(signum: int, frame) -> None:  # noqa: ANN001
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
