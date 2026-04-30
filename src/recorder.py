"""
Audio recorder with energy-based Voice Activity Detection.

After the wake word fires, this module captures speech from the microphone
and returns it as a numpy array once the speaker goes silent (or the safety
timeout is reached).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np

import config

logger = logging.getLogger(__name__)


def compute_rms(audio: np.ndarray) -> float:
    """Return the root-mean-square energy of an audio chunk.

    Parameters
    ----------
    audio:
        1-D float32 audio samples.

    Returns
    -------
    float
        RMS energy value.
    """
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))


def record_until_silence(
    sample_rate: int = config.SAMPLE_RATE,
    channels: int = config.CHANNELS,
    chunk_samples: int = config.AUDIO_CHUNK_SAMPLES,
    silence_threshold: float = config.SILENCE_ENERGY_THRESHOLD,
    silence_duration_ms: int = config.SILENCE_THRESHOLD_MS,
    max_seconds: int = config.MAX_RECORD_SECONDS,
) -> Optional[np.ndarray]:
    """Record from the microphone until silence is detected.

    Parameters
    ----------
    sample_rate:
        Recording sample rate.
    channels:
        Number of audio channels.
    chunk_samples:
        Samples per read chunk.
    silence_threshold:
        RMS energy below which a chunk is considered silent.
    silence_duration_ms:
        How many consecutive milliseconds of silence end the recording.
    max_seconds:
        Hard upper limit on recording duration.

    Returns
    -------
    np.ndarray or None
        Recorded audio as a 1-D float32 array, or ``None`` on failure.
    """
    try:
        import sounddevice as sd  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        logger.error("sounddevice unavailable: %s", exc)
        return None

    frames: list[np.ndarray] = []
    silence_start: Optional[float] = None
    max_end = time.monotonic() + max_seconds
    recording_start = time.monotonic()

    # Grace period: ignore silence for the first N ms so the user has
    # time to start speaking after the wake word.
    grace_ms = 1200  # 1.2 seconds
    # Also require at least some speech before allowing silence to end
    has_speech = False

    logger.debug("Recording started (max %ds, silence %dms, grace %dms)", max_seconds, silence_duration_ms, grace_ms)

    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=channels,
            blocksize=chunk_samples,
            dtype="float32",
        ) as stream:
            while time.monotonic() < max_end:
                chunk, _overflowed = stream.read(chunk_samples)
                mono = chunk[:, 0] if chunk.ndim > 1 else chunk
                frames.append(mono.copy())

                rms = compute_rms(mono)
                elapsed_ms = (time.monotonic() - recording_start) * 1000

                if rms >= silence_threshold:
                    has_speech = True
                    silence_start = None
                elif has_speech and elapsed_ms > grace_ms:
                    # Only start counting silence after grace period
                    # and after we've heard at least some speech
                    if silence_start is None:
                        silence_start = time.monotonic()
                    elif (time.monotonic() - silence_start) * 1000 >= silence_duration_ms:
                        logger.debug("Silence detected — stopping recording")
                        break
    except Exception as exc:  # noqa: BLE001
        logger.error("Recording failed: %s", exc)
        return None

    if not frames:
        return None

    audio = np.concatenate(frames)
    duration = len(audio) / sample_rate
    logger.info("Recorded %.1fs of audio (%d samples)", duration, len(audio))
    return audio
