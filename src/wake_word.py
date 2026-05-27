"""
Wake-word detection with multiple backends.

Supports four detection strategies:

1. **VoskKeywordDetector** (default for "nova") — uses Vosk's grammar-
   constrained recognizer for ultra-low-latency keyword spotting.
   Only listens for "nova" / "hey nova", ignoring everything else.
   ~5% CPU vs. ~30%+ for Whisper-based detection.

2. **PorcupineDetector** — uses Picovoice Porcupine for hardware-
   accelerated wake word detection.  Supports built-in keywords
   (jarvis, computer, alexa, etc.) and custom .ppn models trained
   at console.picovoice.ai (free tier: 3 custom keywords).
   Requires NOVA_PORCUPINE_ACCESS_KEY.

3. **KeywordDetector** (legacy) — buffers short audio windows, runs
   faster-whisper STT on them, and checks for the keyword.  Higher
   CPU usage but actually responds to "Nova".

4. **OpenWakeWordDetector** — uses OpenWakeWord ONNX models (e.g.
   ``hey_jarvis_v0.1``).  Best for wake words that ship with a trained
   model.

The top-level ``WakeWordDetector`` factory automatically picks the right
backend based on ``config.WAKE_WORD_BACKEND``.

Backend priority for "nova" (auto mode):
  1. porcupine — if access key + custom .ppn model are configured
  2. vosk     — lightweight keyword spotting (default)
  3. keyword  — Whisper-based fallback

Change history:
  - 2026-05-26: Added VoskKeywordDetector and PorcupineDetector backends.
                Vosk is now the default for "nova" (much lower CPU usage
                than the Whisper-based KeywordDetector).
                Porcupine backend is ready for when user gets an API key.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable, Optional

import numpy as np

import config
from src.audio import open_input_stream

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Wake words that have a trained OpenWakeWord model
_OPENWAKEWORD_ALIASES: dict[str, str] = {
    "jarvis": "hey_jarvis_v0.1",
    "hey jarvis": "hey_jarvis_v0.1",
    "hey_jarvis": "hey_jarvis_v0.1",
    "rhasspy": "hey_rhasspy_v0.1",
    "hey rhasspy": "hey_rhasspy_v0.1",
    "timer": "timer_v0.1",
    "weather": "weather_v0.1",
}

# Wake words that should use keyword (STT) detection
_KEYWORD_WAKE_WORDS: set[str] = {
    "nova", "hey nova", "hey_nova",
    "ep", "hey ep", "hey_ep", "ep agent",
}

# Porcupine built-in keywords (no custom .ppn needed)
_PORCUPINE_BUILTIN: set[str] = {
    "alexa", "americano", "blueberry", "bumblebee", "computer",
    "grapefruit", "grasshopper", "hey barista", "hey google",
    "hey siri", "jarvis", "ok google", "pico clock", "picovoice",
    "porcupine", "terminator",
}

# Cooldown to avoid rapid re-triggers (seconds)
_DETECTION_COOLDOWN = 2.0


def resolve_wake_word(name: str) -> str:
    """Resolve a friendly wake-word name to the actual OpenWakeWord model name.

    Returns the original name unchanged if there is no OpenWakeWord mapping.
    """
    return _OPENWAKEWORD_ALIASES.get(name.lower().strip(), name)


def _select_backend(wake_word: str, backend_pref: str) -> str:
    """Return the best backend name based on config + wake word.

    Returns one of: 'porcupine', 'vosk', 'keyword', 'openwakeword'.
    """
    pref = backend_pref.lower().strip()

    # Explicit preference — honour it
    if pref in ("porcupine", "vosk", "keyword", "openwakeword"):
        return pref

    # Auto mode
    ww = wake_word.lower().strip()

    # If Porcupine access key is set AND either it's a built-in or a .ppn path exists
    porcupine_key = getattr(config, "PORCUPINE_ACCESS_KEY", "")
    porcupine_ppn = getattr(config, "PORCUPINE_MODEL_PATH", "")
    if porcupine_key:
        if ww in _PORCUPINE_BUILTIN or porcupine_ppn:
            return "porcupine"

    # For "nova" and friends, prefer Vosk (lightweight) over Whisper
    if ww in _KEYWORD_WAKE_WORDS:
        return "vosk"

    # Built-in OpenWakeWord models
    if ww in _OPENWAKEWORD_ALIASES:
        return "openwakeword"

    # Unknown word — try vosk keyword spotting as fallback
    return "vosk"


# =========================================================================
# Vosk Keyword Detector  (lightweight, low-CPU)
# =========================================================================

class VoskKeywordDetector:
    """Listens for a keyword using Vosk's grammar-constrained recognizer.

    This is dramatically lighter than running Whisper: Vosk with a grammar
    constraint only checks if the audio matches a small set of words,
    using ~5% CPU vs. ~30%+ for faster-whisper.

    Parameters
    ----------
    keyword:
        The word/phrase to detect (e.g. ``"nova"``).
    on_wake:
        Callback invoked (off the audio thread) when the keyword is heard.
    vosk_model:
        Vosk model name for auto-download, or path to a local model dir.
    """

    def __init__(
        self,
        keyword: str,
        on_wake: Callable[[], None],
        vosk_model: str = getattr(config, "VOSK_MODEL", "vosk-model-small-en-us-0.15"),
    ) -> None:
        self.keyword = keyword.lower().strip()
        self._match_tokens = self._build_match_tokens(self.keyword)
        self.on_wake = on_wake
        self.vosk_model_name = vosk_model

        self._running = False
        self._paused = False
        self._stream = None
        self._recognizer = None
        self._lock = threading.Lock()
        self._last_detection_time: float = 0.0

    @staticmethod
    def _build_match_tokens(keyword: str) -> list[str]:
        """Return a list of lowercase phrases that count as a detection."""
        tokens = [keyword]
        if " " not in keyword:
            tokens.append(f"hey {keyword}")
        return tokens

    def _matches(self, text: str) -> bool:
        """Check whether Vosk output contains the wake keyword."""
        text_lower = text.lower().strip()
        if not text_lower or text_lower == "[unk]":
            return False
        return any(tok in text_lower for tok in self._match_tokens)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Load Vosk model and begin listening."""
        self._load_model()
        self._running = True
        self._stream = open_input_stream(callback=self._audio_callback)
        if self._stream is None:
            raise RuntimeError("Cannot open microphone input stream")
        self._stream.start()
        logger.info(
            "VoskKeywordDetector started (keyword='%s', grammar=%s)",
            self.keyword, self._match_tokens,
        )

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._recognizer = None
        logger.info("VoskKeywordDetector stopped")

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def _load_model(self) -> None:
        if self._recognizer is not None:
            return

        import json as _json
        import vosk

        vosk.SetLogLevel(-1)  # suppress Kaldi verbose logs

        logger.info("Loading Vosk model '%s' …", self.vosk_model_name)

        import os
        if os.path.isdir(self.vosk_model_name):
            model = vosk.Model(self.vosk_model_name)
        else:
            model = vosk.Model(model_name=self.vosk_model_name)

        # Grammar-constrained recognizer: only listens for our keywords + [unk]
        grammar = self._match_tokens + ["[unk]"]
        self._recognizer = vosk.KaldiRecognizer(
            model, config.SAMPLE_RATE, _json.dumps(grammar)
        )
        self._vosk_model = model  # prevent GC
        logger.info("Vosk keyword model loaded (grammar: %s)", grammar)

    # ------------------------------------------------------------------
    # Audio callback
    # ------------------------------------------------------------------

    def _reopen_stream(self) -> None:
        """Attempt to re-open the audio input stream after a device error."""
        for attempt in range(1, 4):
            logger.info("VoskKeywordDetector audio recovery attempt %d/3 …", attempt)
            time.sleep(2)
            try:
                if self._stream is not None:
                    try:
                        self._stream.stop()
                        self._stream.close()
                    except Exception:
                        pass
                self._stream = open_input_stream(callback=self._audio_callback)
                if self._stream is not None:
                    self._stream.start()
                    logger.info("VoskKeywordDetector audio recovered on attempt %d", attempt)
                    return
            except Exception as exc:
                logger.warning("VoskKeywordDetector recovery attempt %d failed: %s", attempt, exc)
        logger.error("VoskKeywordDetector audio recovery failed after 3 attempts")

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status,
    ) -> None:
        """Called on the sounddevice audio thread with each chunk."""
        if not self._running or self._paused or self._recognizer is None:
            return

        if status:
            logger.warning("VoskKeywordDetector audio issue: %s", status)
            if "input" in str(status).lower() or "overflow" in str(status).lower():
                threading.Thread(target=self._reopen_stream, daemon=True).start()
                return

        # Convert float32 → int16 for Vosk
        audio_int16 = (indata[:, 0] * 32767).astype(np.int16)

        # Feed to Vosk recognizer
        import json as _json
        if self._recognizer.AcceptWaveform(audio_int16.tobytes()):
            result = _json.loads(self._recognizer.Result())
            text = result.get("text", "")
            if self._matches(text):
                now = time.monotonic()
                if now - self._last_detection_time < _DETECTION_COOLDOWN:
                    return
                self._last_detection_time = now
                logger.info(
                    "Vosk keyword '%s' detected: '%s'", self.keyword, text,
                )
                self._paused = True
                threading.Thread(target=self._handle_wake, daemon=True).start()
        else:
            # Check partial results too for faster response
            partial = _json.loads(self._recognizer.PartialResult())
            partial_text = partial.get("partial", "")
            if self._matches(partial_text):
                now = time.monotonic()
                if now - self._last_detection_time < _DETECTION_COOLDOWN:
                    return
                self._last_detection_time = now
                logger.info(
                    "Vosk keyword '%s' detected (partial): '%s'",
                    self.keyword, partial_text,
                )
                self._paused = True
                # Reset recognizer state after detection
                self._recognizer.Reset()
                threading.Thread(target=self._handle_wake, daemon=True).start()

    def _handle_wake(self) -> None:
        """Invoke the user callback."""
        try:
            self.on_wake()
        except Exception as exc:
            logger.error("on_wake callback raised: %s", exc)
        finally:
            self._paused = False


# =========================================================================
# Porcupine Detector  (Picovoice — hardware-accelerated)
# =========================================================================

class PorcupineDetector:
    """Listens for a wake word using Picovoice Porcupine.

    Porcupine is the gold standard for on-device wake word detection:
    - ~0.1% CPU usage
    - <10ms latency
    - Custom keyword training via console.picovoice.ai (free: 3 keywords)

    Requires a Porcupine access key (NOVA_PORCUPINE_ACCESS_KEY).

    For built-in keywords (jarvis, computer, alexa, etc.), no .ppn file needed.
    For custom "nova" keyword, train at console.picovoice.ai and set
    NOVA_PORCUPINE_MODEL_PATH to the .ppn file path.

    Parameters
    ----------
    on_wake:
        Callback invoked when the wake word is heard.
    wake_word:
        Keyword name (must be in Porcupine's built-in list, or provide
        a custom .ppn model via ``model_path``).
    access_key:
        Picovoice access key from console.picovoice.ai.
    model_path:
        Path to a custom .ppn keyword model file.
    sensitivity:
        Detection sensitivity 0.0–1.0 (higher = more sensitive, more false positives).
    """

    def __init__(
        self,
        on_wake: Callable[[], None],
        wake_word: str = config.WAKE_WORD,
        access_key: str = getattr(config, "PORCUPINE_ACCESS_KEY", ""),
        model_path: str = getattr(config, "PORCUPINE_MODEL_PATH", ""),
        sensitivity: float = getattr(config, "PORCUPINE_SENSITIVITY", 0.5),
    ) -> None:
        self.on_wake = on_wake
        self.wake_word = wake_word.lower().strip()
        self.access_key = access_key
        self.model_path = model_path
        self.sensitivity = sensitivity

        self._running = False
        self._paused = False
        self._porcupine = None
        self._stream = None
        self._last_detection_time: float = 0.0

    def start(self) -> None:
        import pvporcupine

        if not self.access_key:
            raise RuntimeError(
                "Porcupine requires an access key. "
                "Set NOVA_PORCUPINE_ACCESS_KEY in your .env file. "
                "Get a free key at https://console.picovoice.ai"
            )

        create_kwargs = {"access_key": self.access_key}

        if self.model_path:
            # Custom .ppn model file
            logger.info("Loading custom Porcupine model: %s", self.model_path)
            create_kwargs["keyword_paths"] = [self.model_path]
            create_kwargs["sensitivities"] = [self.sensitivity]
        elif self.wake_word in _PORCUPINE_BUILTIN:
            # Built-in keyword
            logger.info("Using Porcupine built-in keyword: %s", self.wake_word)
            create_kwargs["keywords"] = [self.wake_word]
            create_kwargs["sensitivities"] = [self.sensitivity]
        else:
            raise RuntimeError(
                f"Wake word '{self.wake_word}' is not a Porcupine built-in. "
                f"Train a custom model at https://console.picovoice.ai and "
                f"set NOVA_PORCUPINE_MODEL_PATH to the .ppn file."
            )

        self._porcupine = pvporcupine.create(**create_kwargs)

        self._running = True
        self._stream = open_input_stream(callback=self._audio_callback)
        if self._stream is None:
            self._porcupine.delete()
            self._porcupine = None
            raise RuntimeError("Cannot open microphone input stream")
        self._stream.start()
        logger.info(
            "PorcupineDetector started (word=%s, sensitivity=%.2f)",
            self.wake_word, self.sensitivity,
        )

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._porcupine is not None:
            self._porcupine.delete()
            self._porcupine = None
        logger.info("PorcupineDetector stopped")

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def _reopen_stream(self) -> None:
        """Attempt to re-open the audio input stream after a device error."""
        for attempt in range(1, 4):
            logger.info("PorcupineDetector audio recovery attempt %d/3 …", attempt)
            time.sleep(2)
            try:
                if self._stream is not None:
                    try:
                        self._stream.stop()
                        self._stream.close()
                    except Exception:
                        pass
                self._stream = open_input_stream(callback=self._audio_callback)
                if self._stream is not None:
                    self._stream.start()
                    logger.info("PorcupineDetector audio recovered on attempt %d", attempt)
                    return
            except Exception as exc:
                logger.warning("PorcupineDetector recovery attempt %d failed: %s", attempt, exc)
        logger.error("PorcupineDetector audio recovery failed after 3 attempts")

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status,
    ) -> None:
        """Called on the sounddevice audio thread with each chunk."""
        if not self._running or self._paused or self._porcupine is None:
            return

        if status:
            logger.warning("PorcupineDetector audio issue: %s", status)
            if "input" in str(status).lower() or "overflow" in str(status).lower():
                threading.Thread(target=self._reopen_stream, daemon=True).start()
                return

        # Porcupine expects int16 PCM at its own frame length
        audio_int16 = (indata[:, 0] * 32767).astype(np.int16)

        # Porcupine processes exactly frame_length samples at a time
        frame_len = self._porcupine.frame_length
        for i in range(0, len(audio_int16) - frame_len + 1, frame_len):
            frame = audio_int16[i:i + frame_len]
            keyword_index = self._porcupine.process(frame)
            if keyword_index >= 0:
                now = time.monotonic()
                if now - self._last_detection_time < _DETECTION_COOLDOWN:
                    continue
                self._last_detection_time = now
                logger.info("Porcupine wake word detected (index=%d)", keyword_index)
                self._paused = True
                threading.Thread(target=self._handle_wake, daemon=True).start()
                return  # Only trigger once per callback

    def _handle_wake(self) -> None:
        try:
            self.on_wake()
        except Exception as exc:
            logger.error("on_wake callback raised: %s", exc)
        finally:
            self._paused = False


# =========================================================================
# Keyword Detector  (faster-whisper STT — legacy, higher CPU)
# =========================================================================

class KeywordDetector:
    """Listens for a keyword by running short STT windows on mic audio.

    NOTE: This is the legacy detector. For "nova", prefer VoskKeywordDetector
    (much lower CPU usage). This is kept as a fallback.

    Parameters
    ----------
    keyword:
        The word/phrase to detect (e.g. ``"nova"``).
    on_wake:
        Callback invoked (off the audio thread) when the keyword is heard.
    buffer_seconds:
        Length of each audio window fed to Whisper.
    energy_threshold:
        RMS energy below which audio is considered silence (skip STT).
    whisper_model:
        faster-whisper model size for detection (``"tiny.en"`` recommended).
    """

    def __init__(
        self,
        keyword: str,
        on_wake: Callable[[], None],
        buffer_seconds: float = config.WAKE_KEYWORD_BUFFER_SEC,
        energy_threshold: float = config.WAKE_KEYWORD_ENERGY_THRESHOLD,
        whisper_model: str = config.WAKE_KEYWORD_WHISPER_MODEL,
    ) -> None:
        self.keyword = keyword.lower().strip()
        # Build match variants: "nova" matches "nova" and "hey nova"
        self._match_tokens = self._build_match_tokens(self.keyword)
        # Pre-compile word-boundary regexes for each token
        self._match_patterns = [
            re.compile(r'\b' + re.escape(tok) + r'\b')
            for tok in self._match_tokens
        ]
        self.on_wake = on_wake
        self.buffer_seconds = buffer_seconds
        self.energy_threshold = energy_threshold
        self.whisper_model_name = whisper_model

        self._running = False
        self._paused = False
        self._stream = None
        self._model = None  # lazy-loaded faster-whisper model
        self._lock = threading.Lock()

        # Ring buffer for audio samples (float32, mono, 16 kHz)
        self._buf_size = int(config.SAMPLE_RATE * self.buffer_seconds)
        self._audio_buf = np.zeros(self._buf_size, dtype=np.float32)
        self._buf_pos = 0  # write cursor (wraps)
        self._samples_since_last = 0  # samples accumulated since last STT run
        self._last_detection_time: float = 0.0

    # ------------------------------------------------------------------

    @staticmethod
    def _build_match_tokens(keyword: str) -> list[str]:
        """Return a list of lowercase phrases that count as a detection."""
        tokens = [keyword]
        # If keyword is a single word, also match "hey <word>"
        if " " not in keyword:
            tokens.append(f"hey {keyword}")
        return tokens

    def _matches(self, text: str) -> bool:
        """Check whether *text* contains the wake keyword as a whole word."""
        text_lower = text.lower()
        return any(self._match_patterns[i].search(text_lower) is not None
                   for i in range(len(self._match_tokens)))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Load Whisper model and begin listening."""
        self._load_model()
        self._running = True
        self._stream = open_input_stream(callback=self._audio_callback)
        if self._stream is None:
            raise RuntimeError("Cannot open microphone input stream")
        self._stream.start()
        logger.info(
            "KeywordDetector started (keyword='%s', buffer=%.1fs, model=%s)",
            self.keyword, self.buffer_seconds, self.whisper_model_name,
        )

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        logger.info("KeywordDetector stopped")

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def _load_model(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel  # noqa: WPS433

        device = "cpu"
        # Apple Silicon: cpu + int8 is the fastest path
        import platform
        if platform.system() == "Darwin" and platform.machine() == "arm64":
            device = "cpu"

        logger.info(
            "Loading keyword-detection Whisper model '%s' on %s …",
            self.whisper_model_name, device,
        )
        self._model = WhisperModel(
            self.whisper_model_name,
            device=device,
            compute_type="int8",
        )
        logger.info("Keyword Whisper model loaded")

    # ------------------------------------------------------------------
    # Audio callback
    # ------------------------------------------------------------------

    def _reopen_stream(self) -> None:
        """Attempt to re-open the audio input stream after a device error.

        Retries up to 3 times with a 2-second delay between attempts.
        """
        for attempt in range(1, 4):
            logger.info("KeywordDetector audio stream recovery attempt %d/3 …", attempt)
            time.sleep(2)
            try:
                if self._stream is not None:
                    try:
                        self._stream.stop()
                        self._stream.close()
                    except Exception:  # noqa: BLE001
                        pass
                self._stream = open_input_stream(callback=self._audio_callback)
                if self._stream is not None:
                    self._stream.start()
                    logger.info("KeywordDetector audio stream recovered on attempt %d", attempt)
                    return
            except Exception as exc:  # noqa: BLE001
                logger.warning("KeywordDetector recovery attempt %d failed: %s", attempt, exc)
        logger.error("KeywordDetector audio stream recovery failed after 3 attempts")

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,  # noqa: ANN001
        status,  # noqa: ANN001
    ) -> None:
        """Called on the sounddevice audio thread with each chunk."""
        if not self._running or self._paused or self._model is None:
            return

        # Detect audio device errors and schedule recovery
        if status:
            logger.warning("KeywordDetector audio stream issue: %s", status)
            if "input" in str(status).lower() or "overflow" in str(status).lower():
                threading.Thread(target=self._reopen_stream, daemon=True).start()
                return

        # Write into ring buffer
        samples = indata[:, 0].astype(np.float32)
        n = len(samples)
        end = self._buf_pos + n
        if end <= self._buf_size:
            self._audio_buf[self._buf_pos:end] = samples
        else:
            first = self._buf_size - self._buf_pos
            self._audio_buf[self._buf_pos:] = samples[:first]
            self._audio_buf[:n - first] = samples[first:]
        self._buf_pos = end % self._buf_size
        self._samples_since_last += n

        # Only run STT once we've accumulated a full buffer window
        if self._samples_since_last < self._buf_size:
            return
        self._samples_since_last = 0

        # Energy gate — skip silent buffers
        rms = float(np.sqrt(np.mean(self._audio_buf ** 2)))
        if rms < self.energy_threshold:
            return

        # Cooldown — don't re-trigger too quickly
        now = time.monotonic()
        if now - self._last_detection_time < _DETECTION_COOLDOWN:
            return

        # Snapshot the buffer (avoid mutation while STT runs)
        audio_snapshot = self._audio_buf.copy()

        # Run STT off the audio thread to avoid blocking
        threading.Thread(
            target=self._run_stt, args=(audio_snapshot, now), daemon=True
        ).start()

    def _run_stt(self, audio: np.ndarray, timestamp: float) -> None:
        """Transcribe *audio* and check for the keyword."""
        if self._model is None or not self._running:
            return
        try:
            segments, _info = self._model.transcribe(
                audio,
                beam_size=1,
                best_of=1,
                language="en",
                without_timestamps=True,
                vad_filter=False,  # we already did energy gating
            )
            text = " ".join(seg.text for seg in segments).strip()
            if not text:
                return
            logger.debug("Keyword STT heard: '%s'", text)
            if self._matches(text):
                # Cooldown check again (thread-safe-ish)
                now = time.monotonic()
                if now - self._last_detection_time < _DETECTION_COOLDOWN:
                    return
                self._last_detection_time = now
                logger.info(
                    "Keyword '%s' detected in transcription: '%s'",
                    self.keyword, text,
                )
                self._paused = True
                self._handle_wake()
        except Exception as exc:
            logger.error("KeywordDetector STT error: %s", exc)

    def _handle_wake(self) -> None:
        """Invoke the user callback."""
        try:
            self.on_wake()
        except Exception as exc:
            logger.error("on_wake callback raised: %s", exc)
        finally:
            self._paused = False


# =========================================================================
# OpenWakeWord Detector  (original behaviour)
# =========================================================================

class OpenWakeWordDetector:
    """Listens for a wake word using an OpenWakeWord ONNX model.

    Parameters
    ----------
    on_wake:
        Callable invoked when the wake word is heard.
    wake_word:
        Friendly name resolved via ``_OPENWAKEWORD_ALIASES``.
    confidence_threshold:
        Minimum detection confidence (0.0–1.0).
    """

    def __init__(
        self,
        on_wake: Callable[[], None],
        wake_word: str = config.WAKE_WORD,
        confidence_threshold: float = config.WAKE_WORD_CONFIDENCE,
    ) -> None:
        self.on_wake = on_wake
        self.wake_word = wake_word
        self.confidence_threshold = confidence_threshold

        self._running = False
        self._stream = None
        self._model = None
        self._lock = threading.Lock()
        self._paused = False

    def start(self) -> None:
        resolved = resolve_wake_word(self.wake_word)
        logger.info(
            "Resolving wake word '%s' -> OWW model '%s'",
            self.wake_word, resolved,
        )
        try:
            from openwakeword.model import Model  # noqa: WPS433

            self._model = Model(
                wakeword_models=[resolved],
                inference_framework="onnx",
            )
            self._resolved_wake_word = resolved
        except Exception as exc:
            logger.error("Failed to load OpenWakeWord model: %s", exc)
            raise RuntimeError(
                f"Cannot initialise wake-word model '{self.wake_word}': {exc}"
            ) from exc

        self._running = True
        self._stream = open_input_stream(callback=self._audio_callback)
        if self._stream is None:
            raise RuntimeError("Cannot open microphone input stream")

        self._stream.start()
        logger.info(
            "OpenWakeWordDetector started (word=%s, threshold=%.2f)",
            self.wake_word, self.confidence_threshold,
        )

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        logger.info("OpenWakeWordDetector stopped")

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def _reopen_stream(self) -> None:
        """Attempt to re-open the audio input stream after a device error.

        Retries up to 3 times with a 2-second delay between attempts.
        """
        for attempt in range(1, 4):
            logger.info("OpenWakeWordDetector audio stream recovery attempt %d/3 …", attempt)
            time.sleep(2)
            try:
                if self._stream is not None:
                    try:
                        self._stream.stop()
                        self._stream.close()
                    except Exception:  # noqa: BLE001
                        pass
                self._stream = open_input_stream(callback=self._audio_callback)
                if self._stream is not None:
                    self._stream.start()
                    logger.info("OpenWakeWordDetector audio stream recovered on attempt %d", attempt)
                    return
            except Exception as exc:  # noqa: BLE001
                logger.warning("OpenWakeWordDetector recovery attempt %d failed: %s", attempt, exc)
        logger.error("OpenWakeWordDetector audio stream recovery failed after 3 attempts")

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if not self._running or self._paused or self._model is None:
            return

        # Detect audio device errors and schedule recovery
        if status:
            logger.warning("OpenWakeWordDetector audio stream issue: %s", status)
            if "input" in str(status).lower() or "overflow" in str(status).lower():
                threading.Thread(target=self._reopen_stream, daemon=True).start()
                return

        audio_int16 = (indata[:, 0] * 32767).astype(np.int16)
        prediction = self._model.predict(audio_int16)
        resolved = getattr(self, "_resolved_wake_word", self.wake_word)
        score = prediction.get(resolved, 0.0)
        if score >= self.confidence_threshold:
            logger.info("Wake word detected (confidence=%.3f)", score)
            self._model.reset()
            self._paused = True
            threading.Thread(target=self._handle_wake, daemon=True).start()

    def _handle_wake(self) -> None:
        try:
            self.on_wake()
        except Exception as exc:
            logger.error("on_wake callback raised: %s", exc)
        finally:
            self._paused = False


# =========================================================================
# Unified WakeWordDetector  (public API — backward compatible)
# =========================================================================

class WakeWordDetector:
    """Facade that picks the right detection backend automatically.

    Maintains the same public API as before (start / stop / pause / resume)
    so existing callers (``NovaPipeline``) don't need changes.

    Backend selection (auto mode):
      1. porcupine — if NOVA_PORCUPINE_ACCESS_KEY is set
      2. vosk      — lightweight keyword spotting (new default for "nova")
      3. keyword   — Whisper-based fallback
      4. openwakeword — for words with trained .onnx models

    Parameters
    ----------
    on_wake:
        Callable invoked (in a worker thread) when the wake word is heard.
    wake_word:
        Name of the wake word to detect.
    confidence_threshold:
        Minimum detection confidence (for OpenWakeWord backend).
    backend:
        ``'auto'``, ``'vosk'``, ``'porcupine'``, ``'keyword'``, or
        ``'openwakeword'``.  Default from ``config.WAKE_WORD_BACKEND``.
    """

    def __init__(
        self,
        on_wake: Callable[[], None],
        wake_word: str = config.WAKE_WORD,
        confidence_threshold: float = config.WAKE_WORD_CONFIDENCE,
        backend: str = config.WAKE_WORD_BACKEND,
    ) -> None:
        self.on_wake = on_wake
        self.wake_word = wake_word
        self.confidence_threshold = confidence_threshold
        self._backend_name = _select_backend(wake_word, backend)
        self._detector: (
            VoskKeywordDetector
            | PorcupineDetector
            | KeywordDetector
            | OpenWakeWordDetector
            | None
        ) = None

    @property
    def backend_name(self) -> str:
        return self._backend_name

    @property
    def _running(self) -> bool:
        """Expose inner detector's running state for watchdog monitoring."""
        if self._detector is not None:
            return self._detector._running
        return False

    @_running.setter
    def _running(self, value: bool) -> None:
        if self._detector is not None:
            self._detector._running = value

    def start(self) -> None:
        logger.info(
            "WakeWordDetector routing '%s' to backend '%s'",
            self.wake_word, self._backend_name,
        )
        if self._backend_name == "vosk":
            self._detector = VoskKeywordDetector(
                keyword=self.wake_word,
                on_wake=self.on_wake,
            )
        elif self._backend_name == "porcupine":
            self._detector = PorcupineDetector(
                on_wake=self.on_wake,
                wake_word=self.wake_word,
            )
        elif self._backend_name == "keyword":
            self._detector = KeywordDetector(
                keyword=self.wake_word,
                on_wake=self.on_wake,
            )
        else:
            self._detector = OpenWakeWordDetector(
                on_wake=self.on_wake,
                wake_word=self.wake_word,
                confidence_threshold=self.confidence_threshold,
            )
        self._detector.start()

    def stop(self) -> None:
        if self._detector is not None:
            self._detector.stop()
            self._detector = None

    def pause(self) -> None:
        if self._detector is not None:
            self._detector.pause()

    def resume(self) -> None:
        if self._detector is not None:
            self._detector.resume()
