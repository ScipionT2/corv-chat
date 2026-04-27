"""
Text-to-Speech module with Piper TTS primary and macOS ``say`` fallback.

On ARM64 macOS where piper-tts wheels are unavailable, the module
gracefully falls back to the built-in ``say`` command.

Supports multiple macOS voices — configurable via ``--voice`` CLI flag,
``JARVIS_VOICE`` environment variable, or ``JARVIS_SAY_VOICE``.
"""

from __future__ import annotations

import logging
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

_PIPER_AVAILABLE: Optional[bool] = None


def _check_piper() -> bool:
    """Return ``True`` if piper-tts can be imported."""
    global _PIPER_AVAILABLE  # noqa: WPS420
    if _PIPER_AVAILABLE is not None:
        return _PIPER_AVAILABLE
    try:
        import piper  # noqa: F401, WPS433

        _PIPER_AVAILABLE = True
    except Exception:  # noqa: BLE001
        _PIPER_AVAILABLE = False
    return _PIPER_AVAILABLE


def _say_available() -> bool:
    """Return ``True`` if the macOS ``say`` command exists."""
    return shutil.which("say") is not None


def _select_backend(preference: str = config.TTS_BACKEND) -> str:
    """Resolve the TTS backend to use.

    Parameters
    ----------
    preference:
        ``'auto'``, ``'piper'``, or ``'say'``.

    Returns
    -------
    str
        The selected backend name (``'piper'`` or ``'say'``).

    Raises
    ------
    RuntimeError
        If no usable backend is available.
    """
    if preference == "piper":
        if _check_piper():
            return "piper"
        raise RuntimeError("piper-tts requested but not importable")
    if preference == "say":
        if _say_available():
            return "say"
        raise RuntimeError("macOS 'say' requested but not found on PATH")

    # auto: try piper first, then say
    if _check_piper():
        return "piper"
    if _say_available():
        return "say"
    raise RuntimeError("No TTS backend available (tried piper-tts and macOS say)")


# ---------------------------------------------------------------------------
# Voice listing
# ---------------------------------------------------------------------------


def get_available_voices() -> list[dict[str, str]]:
    """Return a list of available macOS ``say`` voices.

    Each entry is a dict with keys ``name``, ``language``, and optionally
    ``description``.  Returns an empty list on non-macOS systems or if
    the ``say`` command is unavailable.

    Returns
    -------
    list[dict[str, str]]
        Available voices.
    """
    if not _say_available():
        return []

    try:
        result = subprocess.run(
            ["say", "-v", "?"],
            capture_output=True,
            text=True,
            timeout=10,
        )  # noqa: S603
        voices: list[dict[str, str]] = []
        for line in result.stdout.strip().splitlines():
            # Format: "Name      lang_REGION  # Sample text"
            match = re.match(r"^(\S+(?:\s+\S+)*)\s+(\w{2}_\w+)\s*#?\s*(.*)?$", line.strip())
            if match:
                name = match.group(1).strip()
                lang = match.group(2).strip()
                desc = match.group(3).strip() if match.group(3) else ""
                voices.append({"name": name, "language": lang, "description": desc})
        return voices
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Failed to list voices: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class TextToSpeech:
    """Unified TTS interface with automatic backend selection.

    Parameters
    ----------
    backend:
        ``'auto'``, ``'piper'``, or ``'say'``.
    piper_voice:
        Piper voice model name.
    say_voice:
        macOS ``say`` voice name.
    """

    def __init__(
        self,
        backend: str = config.TTS_BACKEND,
        piper_voice: str = config.PIPER_VOICE,
        say_voice: Optional[str] = None,
    ) -> None:
        self._backend_pref = backend
        self.piper_voice = piper_voice
        # Priority: explicit say_voice param > JARVIS_VOICE env > JARVIS_SAY_VOICE config
        import os
        self.say_voice = say_voice or os.environ.get("JARVIS_VOICE") or config.MACOS_SAY_VOICE
        self._backend: Optional[str] = None

    @property
    def backend(self) -> str:
        """Resolve and cache the active backend."""
        if self._backend is None:
            self._backend = _select_backend(self._backend_pref)
            logger.info("TTS backend: %s", self._backend)
        return self._backend

    def speak(self, text: str) -> None:
        """Synthesise and play *text* through the selected backend.

        Parameters
        ----------
        text:
            The text to speak aloud.
        """
        if not text or not text.strip():
            return

        text = text.strip()
        logger.debug("Speaking (%s): %s", self.backend, text[:80])

        if self.backend == "piper":
            self._speak_piper(text)
        else:
            self._speak_say(text)

    # ------------------------------------------------------------------
    # Backends
    # ------------------------------------------------------------------

    def _speak_piper(self, text: str) -> None:
        """Synthesise with piper-tts and play via sounddevice."""
        try:
            import piper  # noqa: WPS433
            import numpy as np  # noqa: WPS433

            voice = piper.PiperVoice.load(self.piper_voice)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = Path(tmp.name)

            voice.synthesize(text, str(tmp_path))

            # Play via sounddevice
            import soundfile as sf  # noqa: WPS433
            from src.audio import play_audio  # noqa: WPS433

            data, sr = sf.read(str(tmp_path), dtype="float32")
            play_audio(data, sample_rate=sr, blocking=True)
            tmp_path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.error("Piper TTS failed: %s — falling back to say", exc)
            if _say_available():
                self._speak_say(text)

    def _speak_say(self, text: str) -> None:
        """Speak using macOS ``say`` command."""
        try:
            cmd = ["say", "-v", self.say_voice, text]
            subprocess.run(cmd, check=True, timeout=60)  # noqa: S603
        except FileNotFoundError:
            logger.error("macOS 'say' command not found")
        except subprocess.TimeoutExpired:
            logger.warning("say command timed out")
        except subprocess.CalledProcessError as exc:
            logger.error("say command failed: %s", exc)
