"""
Speech-to-Text module using faster-whisper.

Loads a Whisper model once at startup and reuses it for all subsequent
transcription requests.  Designed for low-latency, fully-local operation.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

import config

logger = logging.getLogger(__name__)


class SpeechToText:
    """Local speech-to-text engine backed by faster-whisper.

    Parameters
    ----------
    model_name:
        Whisper model size / name (e.g. ``"base.en"``).
    device:
        Compute device (``"cpu"`` or ``"cuda"``).
    compute_type:
        Quantisation type (``"int8"``, ``"float16"``, …).
    """

    def __init__(
        self,
        model_name: str = config.WHISPER_MODEL,
        device: str = config.WHISPER_DEVICE,
        compute_type: str = config.WHISPER_COMPUTE_TYPE,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load the Whisper model into memory.

        Call once at startup.  Subsequent calls are no-ops.
        """
        if self._model is not None:
            return

        logger.info(
            "Loading Whisper model '%s' (device=%s, compute=%s) …",
            self.model_name,
            self.device,
            self.compute_type,
        )
        from faster_whisper import WhisperModel  # noqa: WPS433

        self._model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
        )
        logger.info("Whisper model loaded")

    def transcribe(self, audio: np.ndarray, sample_rate: int = config.SAMPLE_RATE) -> Optional[str]:
        """Transcribe a numpy audio array to text.

        Parameters
        ----------
        audio:
            1-D float32 audio samples.
        sample_rate:
            The sample rate of *audio* (must match the model expectation —
            Whisper expects 16 kHz).

        Returns
        -------
        str or None
            Transcribed text, or ``None`` if transcription failed or was empty.
        """
        if self._model is None:
            self.load()

        if audio is None or audio.size == 0:
            logger.warning("Empty audio passed to transcribe()")
            return None

        try:
            segments, info = self._model.transcribe(
                audio,
                beam_size=5,
                language="en",
                vad_filter=True,
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
            if not text:
                logger.info("Transcription returned empty text")
                return None
            logger.info("Transcribed: %s", text)
            return text
        except Exception as exc:  # noqa: BLE001
            logger.error("Transcription failed: %s", exc)
            return None
