"""Text-to-Speech module with Piper TTS primary and macOS ``say`` fallback.

On ARM64 macOS where piper-tts wheels are unavailable, the module
gracefully falls back to the built-in ``say`` command.

Supports multiple macOS voices — configurable via ``--voice`` CLI flag,
``EP_VOICE`` / ``EP_SAY_VOICE`` environment variables.

Includes ``StreamingTTS`` and ``SentenceBuffer`` for overlapping LLM
token generation with TTS playback (sentence-level streaming).
"""

from __future__ import annotations

import logging
import platform
import queue
import re
import shutil
import subprocess
import tempfile
import threading
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
        # Priority: explicit say_voice param > EP_VOICE env > config default
        import os
        self.say_voice = say_voice or os.environ.get("NOVA_VOICE") or os.environ.get("EP_VOICE") or os.environ.get("JARVIS_VOICE") or config.MACOS_SAY_VOICE
        self._backend: Optional[str] = None
        self._current_process: Optional[subprocess.Popen] = None
        self._process_lock = threading.Lock()
        self._interrupted = False

    @property
    def backend(self) -> str:
        """Resolve and cache the active backend."""
        if self._backend is None:
            self._backend = _select_backend(self._backend_pref)
            logger.info("TTS backend: %s", self._backend)
        return self._backend

    @property
    def is_speaking(self) -> bool:
        """Return ``True`` if TTS is currently playing audio."""
        with self._process_lock:
            if self._current_process is not None:
                return self._current_process.poll() is None
        return False

    @property
    def was_interrupted(self) -> bool:
        """Return ``True`` if the last speak() call was interrupted via stop()."""
        return self._interrupted

    def stop(self) -> None:
        """Immediately stop any in-progress speech playback."""
        with self._process_lock:
            if self._current_process is not None and self._current_process.poll() is None:
                logger.info("Interrupting TTS playback")
                self._interrupted = True
                try:
                    self._current_process.kill()
                    self._current_process.wait(timeout=2)
                except Exception:  # noqa: BLE001
                    pass
                self._current_process = None

    def speak(self, text: str) -> None:
        """Synthesise and play *text* through the selected backend.

        Parameters
        ----------
        text:
            The text to speak aloud.
        """
        if not text or not text.strip():
            return

        self._interrupted = False
        text = text.strip()
        logger.debug("Speaking (%s): %s", self.backend, text[:80])

        if self.backend == "piper":
            self._speak_piper(text)
        else:
            self._speak_say(text)

    def speak_streamed(self, text_generator) -> bool:
        """Speak text as it arrives from a generator, sentence by sentence.

        Buffers tokens until a sentence boundary (``.``, ``!``, ``?``,
        newline, or buffer > 200 chars), then speaks each sentence while
        continuing to buffer the next one.

        Parameters
        ----------
        text_generator:
            An iterable/generator that yields string tokens.

        Returns
        -------
        bool
            ``True`` if all speech completed, ``False`` if interrupted.
        """
        import re as _re

        _SENTENCE_END = _re.compile(r'[.!?]\s|\n')

        self._interrupted = False
        buffer = ""
        full_text = ""

        def _flush(chunk: str) -> bool:
            """Speak a chunk. Returns False if interrupted."""
            chunk = chunk.strip()
            if not chunk:
                return True
            if self._interrupted:
                return False
            self.speak(chunk)
            return not self._interrupted

        try:
            for token in text_generator:
                if self._interrupted:
                    # Drain remaining tokens to allow the generator to
                    # record the full reply in history even on interrupt.
                    full_text += token
                    continue

                buffer += token
                full_text += token

                # Check for sentence boundary or long buffer
                if _SENTENCE_END.search(buffer) or len(buffer) > 200:
                    if not _flush(buffer):
                        # Interrupted — keep draining tokens but stop speaking
                        buffer = ""
                        continue
                    buffer = ""
        except Exception as exc:  # noqa: BLE001
            logger.error("speak_streamed generator error: %s", exc)

        # Flush any remaining text
        if buffer and not self._interrupted:
            _flush(buffer)

        return not self._interrupted

    def create_streaming_session(self) -> "StreamingTTS":
        """Create a concurrent streaming TTS session.

        Returns a :class:`StreamingTTS` that queues sentences and speaks
        them in a background thread, allowing LLM token generation to
        overlap with audio playback.

        Usage::

            stream = tts.create_streaming_session()
            for token in llm_tokens:
                stream.add_token(token)
            stream.finish()  # flushes remaining buffer and waits
            stream.shutdown()

        Returns
        -------
        StreamingTTS
        """
        return StreamingTTS(self)

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
        """Speak using macOS ``say`` command.

        Uses Popen instead of run so the process can be killed mid-speech
        for interrupt support.
        """
        try:
            cmd = ["say", "-v", self.say_voice, text]
            proc = subprocess.Popen(cmd)  # noqa: S603
            with self._process_lock:
                self._current_process = proc
            proc.wait(timeout=60)
        except FileNotFoundError:
            logger.error("macOS 'say' command not found")
        except subprocess.TimeoutExpired:
            logger.warning("say command timed out")
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            logger.error("say command failed: %s", exc)
        finally:
            with self._process_lock:
                self._current_process = None


# ---------------------------------------------------------------------------
# Streaming TTS — concurrent sentence queue
# ---------------------------------------------------------------------------


class StreamingTTS:
    """Concurrent streaming TTS with a background worker thread.

    Queues sentences and speaks them sequentially in a daemon thread,
    overlapping LLM token generation with TTS audio playback.  Includes
    a built-in :class:`SentenceBuffer` so callers can feed raw tokens
    via :meth:`add_token`.

    Parameters
    ----------
    tts:
        The parent :class:`TextToSpeech` instance used for playback.
    max_buffer_chars:
        Force-flush the sentence buffer after this many characters even
        if no sentence boundary is found (prevents unbounded buffering).
    """

    def __init__(self, tts: TextToSpeech, max_buffer_chars: int = 200) -> None:
        self._tts = tts
        self._queue: queue.Queue[Optional[str]] = queue.Queue()
        self._running = True
        self._interrupted = False
        self._buffer = SentenceBuffer(max_chars=max_buffer_chars)
        self._full_text = ""
        self._thread = threading.Thread(
            target=self._worker, name="streaming-tts-worker", daemon=True,
        )
        self._thread.start()

    # -- public API -----------------------------------------------------

    def add_token(self, token: str) -> None:
        """Feed a token from the LLM.

        Complete sentences are automatically flushed to the speech queue.
        """
        self._full_text += token
        if self._interrupted:
            return
        sentences = self._buffer.feed(token)
        for sentence in sentences:
            self._queue.put(sentence)

    def finish(self) -> None:
        """Flush remaining buffer and wait for all queued speech to finish."""
        if not self._interrupted:
            remaining = self._buffer.flush()
            if remaining:
                self._queue.put(remaining)
        # Sentinel — tells the worker to exit after draining
        self._queue.put(None)
        self._thread.join(timeout=120)

    def stop(self) -> None:
        """Immediately interrupt: stop current playback and clear the queue."""
        self._interrupted = True
        self._running = False
        # Clear pending sentences
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        # Kill active say process
        self._tts.stop()
        # Unblock the worker
        self._queue.put(None)

    def shutdown(self) -> None:
        """Gracefully shut down the worker thread (idempotent)."""
        self._running = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=3)

    @property
    def full_text(self) -> str:
        """All tokens received so far, concatenated."""
        return self._full_text

    @property
    def was_interrupted(self) -> bool:
        return self._interrupted

    # -- worker ---------------------------------------------------------

    def _worker(self) -> None:
        """Background thread: pull sentences from the queue and speak them."""
        while self._running:
            try:
                text = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            if text is None:
                break
            if self._interrupted:
                self._queue.task_done()
                continue
            try:
                self._tts.speak(text)
            except Exception as exc:  # noqa: BLE001
                logger.error("StreamingTTS worker error: %s", exc)
            self._queue.task_done()


class SentenceBuffer:
    """Accumulates tokens and emits complete sentences.

    A sentence is delimited by ``. ``, ``! ``, ``? ``, ``\n``, or when
    the buffer exceeds *max_chars*.

    Parameters
    ----------
    max_chars:
        Force-flush threshold.
    """

    _SENTENCE_RE = re.compile(r'(?<=[.!?])\s+|\n')

    def __init__(self, max_chars: int = 200) -> None:
        self._buf = ""
        self._max = max_chars

    def feed(self, token: str) -> list[str]:
        """Add *token* and return any complete sentences.

        Returns
        -------
        list[str]
            Zero or more non-empty sentence strings.
        """
        if not token:
            return []

        self._buf += token
        sentences: list[str] = []

        # Split on sentence boundaries
        parts = self._SENTENCE_RE.split(self._buf)
        if len(parts) > 1:
            # Everything except the last fragment is a complete sentence
            for part in parts[:-1]:
                s = part.strip()
                if s:
                    sentences.append(s)
            self._buf = parts[-1]

        # Force-flush on long buffers (e.g. no punctuation)
        if len(self._buf) > self._max:
            s = self._buf.strip()
            if s:
                sentences.append(s)
            self._buf = ""

        return sentences

    def flush(self) -> str:
        """Return whatever remains in the buffer and reset."""
        text = self._buf.strip()
        self._buf = ""
        return text
