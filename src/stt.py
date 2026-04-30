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
        self.device = self._resolve_device(device)
        self.compute_type = compute_type
        self._model = None

    @staticmethod
    def _resolve_device(device: str) -> str:
        """Resolve 'auto' to the best available device.

        Apple Silicon → 'cpu' with int8 (faster-whisper uses CoreML/Accelerate
        under the hood on ARM). CUDA → 'cuda' if available.
        """
        if device != "auto":
            return device

        import platform
        if platform.system() == "Darwin" and platform.machine() == "arm64":
            # faster-whisper on Apple Silicon: 'cpu' with int8 uses
            # NEON/Accelerate — this is actually the fastest path since
            # faster-whisper doesn't support Metal directly yet.
            # The key win is using int8 quantization (already set).
            return "cpu"

        # Check for CUDA
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
        except ImportError:
            pass

        return "cpu"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load the Whisper model into memory.

        Call once at startup.  Subsequent calls are no-ops.
        In offline mode, forces local-only loading (no HuggingFace calls).
        """
        if self._model is not None:
            return

        logger.info(
            "Loading Whisper model '%s' (device=%s, compute=%s) …",
            self.model_name,
            self.device,
            self.compute_type,
        )

        # Block HuggingFace network calls in offline mode
        import os
        if getattr(config, "OFFLINE_MODE", False):
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            logger.info("Offline mode: using cached Whisper model only")

        from faster_whisper import WhisperModel  # noqa: WPS433

        self._model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            # local_files_only ensures no network fetch attempt
            local_files_only=getattr(config, "OFFLINE_MODE", False),
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
