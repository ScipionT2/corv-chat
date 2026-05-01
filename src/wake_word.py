"""
Wake-word detection using OpenWakeWord.

Continuously streams microphone audio and fires a callback when the
configured wake word is detected with sufficient confidence.

Note: Uses the "hey_jarvis" OpenWakeWord model as the trigger.
The display name is EP Agent but the acoustic model remains jarvis-based.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import numpy as np

import config
from src.audio import generate_blip, open_input_stream, play_audio

logger = logging.getLogger(__name__)

# Map friendly wake-word names to actual OpenWakeWord model names
# "jarvis" is kept as the acoustic model — it's what OpenWakeWord ships.
_WAKE_WORD_ALIASES: dict[str, str] = {
    "jarvis": "hey_jarvis_v0.1",
    "hey jarvis": "hey_jarvis_v0.1",
    "hey_jarvis": "hey_jarvis_v0.1",
    "ep": "hey_jarvis_v0.1",
    "hey ep": "hey_jarvis_v0.1",
    "hey_ep": "hey_jarvis_v0.1",
    "ep agent": "hey_jarvis_v0.1",
    "rhasspy": "hey_rhasspy_v0.1",
    "hey rhasspy": "hey_rhasspy_v0.1",
    "timer": "timer_v0.1",
    "weather": "weather_v0.1",
}


def resolve_wake_word(name: str) -> str:
    """Resolve a friendly wake-word name to the actual OpenWakeWord model name."""
    return _WAKE_WORD_ALIASES.get(name.lower().strip(), name)


class WakeWordDetector:
    """Listens for a wake word on the default microphone.

    Parameters
    ----------
    on_wake:
        Callable invoked (in a worker thread) when the wake word is heard.
    wake_word:
        Name of the wake word to detect (must be an OpenWakeWord model).
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
        self._blip = generate_blip()
        self._lock = threading.Lock()
        self._paused = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Load the model and begin listening."""
        resolved = resolve_wake_word(self.wake_word)
        logger.info(
            "Resolving wake word '%s' -> model '%s'",
            self.wake_word, resolved,
        )
        try:
            from openwakeword.model import Model  # noqa: WPS433

            self._model = Model(
                wakeword_models=[resolved],
                inference_framework="onnx",
            )
            # Store the resolved name for prediction lookups
            self._resolved_wake_word = resolved
        except Exception as exc:  # noqa: BLE001
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
            "Wake-word detector started (word=%s, threshold=%.2f)",
            self.wake_word,
            self.confidence_threshold,
        )

    def stop(self) -> None:
        """Stop listening and release resources."""
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None
        logger.info("Wake-word detector stopped")

    def pause(self) -> None:
        """Temporarily ignore detections (e.g. while EP Agent is speaking)."""
        self._paused = True

    def resume(self) -> None:
        """Resume detection after a pause."""
        self._paused = False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:  # noqa: ANN001
        """sounddevice input-stream callback — runs on the audio thread."""
        if not self._running or self._paused or self._model is None:
            return

        # OpenWakeWord expects int16 samples
        audio_int16 = (indata[:, 0] * 32767).astype(np.int16)
        prediction = self._model.predict(audio_int16)

        resolved = getattr(self, '_resolved_wake_word', self.wake_word)
        score = prediction.get(resolved, 0.0)
        if score >= self.confidence_threshold:
            logger.info("Wake word detected (confidence=%.3f)", score)
            # Reset so we don't re-trigger immediately
            self._model.reset()
            self._paused = True  # pause while handling
            # Play blip & fire callback off the audio thread
            threading.Thread(target=self._handle_wake, daemon=True).start()

    def _handle_wake(self) -> None:
        """Play the activation blip, then invoke the user callback."""
        try:
            play_audio(self._blip, blocking=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.on_wake()
        except Exception as exc:  # noqa: BLE001
            logger.error("on_wake callback raised: %s", exc)
        finally:
            self._paused = False
