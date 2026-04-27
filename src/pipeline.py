"""
Jarvis Pipeline — orchestrates the full voice-interaction loop.

Wake word → Record → Transcribe → LLM → Speak
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import config
from src.llm import OllamaClient
from src.recorder import record_until_silence
from src.stt import SpeechToText
from src.tts import TextToSpeech
from src.wake_word import WakeWordDetector

logger = logging.getLogger(__name__)


class JarvisPipeline:
    """End-to-end voice assistant pipeline.

    Parameters
    ----------
    wake_word:
        Wake-word string (OpenWakeWord model name).
    ollama_model:
        Ollama model tag.
    whisper_model:
        Whisper model size / name.
    tts_backend:
        TTS backend preference (``'auto'``, ``'piper'``, ``'say'``).
    """

    def __init__(
        self,
        wake_word: str = config.WAKE_WORD,
        ollama_model: str = config.OLLAMA_MODEL,
        whisper_model: str = config.WHISPER_MODEL,
        tts_backend: str = config.TTS_BACKEND,
    ) -> None:
        self.stt = SpeechToText(model_name=whisper_model)
        self.llm = OllamaClient(model=ollama_model)
        self.tts = TextToSpeech(backend=tts_backend)
        self.detector: Optional[WakeWordDetector] = None

        self._wake_word = wake_word
        self._running = False
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Initialise components and begin listening for the wake word."""
        logger.info("Starting Jarvis pipeline …")

        # Pre-load the Whisper model so first interaction is fast
        self.stt.load()

        self.detector = WakeWordDetector(
            on_wake=self.on_wake,
            wake_word=self._wake_word,
        )
        self.detector.start()

        self._running = True
        self._stop_event.clear()
        logger.info("Jarvis pipeline is running — say '%s' to begin", self._wake_word)

    def stop(self) -> None:
        """Cleanly shut everything down."""
        logger.info("Stopping Jarvis pipeline …")
        self._running = False
        self._stop_event.set()
        if self.detector is not None:
            self.detector.stop()
        logger.info("Jarvis pipeline stopped")

    def wait(self) -> None:
        """Block until :meth:`stop` is called."""
        self._stop_event.wait()

    # ------------------------------------------------------------------
    # Core interaction
    # ------------------------------------------------------------------

    def on_wake(self) -> None:
        """Handle a single wake-word activation.

        Called on a worker thread by :class:`WakeWordDetector`.
        """
        if not self._running:
            return

        logger.info("Wake word activated — recording …")

        # 1. Record speech
        audio = record_until_silence()
        if audio is None or audio.size == 0:
            self._speak_error("Sorry, I didn't catch that.")
            return

        # 2. Transcribe
        text = self.stt.transcribe(audio)
        if not text:
            self._speak_error("Sorry, I couldn't understand what you said.")
            return

        logger.info("User said: %s", text)

        # 3. Query LLM
        reply = self.llm.chat(text)
        if not reply:
            self._speak_error("Sorry, I'm having trouble thinking right now.")
            return

        # 4. Speak response
        logger.info("Jarvis says: %s", reply[:120])
        self.tts.speak(reply)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _speak_error(self, message: str) -> None:
        """Speak an error message to the user."""
        logger.warning(message)
        try:
            self.tts.speak(message)
        except Exception:  # noqa: BLE001
            pass
