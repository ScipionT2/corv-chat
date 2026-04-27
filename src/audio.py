"""
Audio playback and utility helpers.

Provides thin wrappers around ``sounddevice`` for recording and playback,
plus a simple activation-blip generator.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

import config

logger = logging.getLogger(__name__)


def generate_blip(
    frequency_hz: int = config.BLIP_FREQUENCY_HZ,
    duration_ms: int = config.BLIP_DURATION_MS,
    sample_rate: int = config.SAMPLE_RATE,
    amplitude: float = 0.4,
) -> np.ndarray:
    """Generate a short sine-wave beep as a float32 numpy array.

    Parameters
    ----------
    frequency_hz:
        Tone frequency in Hz.
    duration_ms:
        Duration in milliseconds.
    sample_rate:
        Audio sample rate.
    amplitude:
        Peak amplitude (0.0–1.0).

    Returns
    -------
    np.ndarray
        1-D float32 array of audio samples.
    """
    t = np.linspace(
        0, duration_ms / 1000.0, int(sample_rate * duration_ms / 1000), endpoint=False
    )
    # Apply a quick fade-in/out to avoid clicks
    blip = (amplitude * np.sin(2 * np.pi * frequency_hz * t)).astype(np.float32)
    fade_samples = min(len(blip) // 4, int(sample_rate * 0.01))
    if fade_samples > 0:
        fade_in = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
        fade_out = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
        blip[:fade_samples] *= fade_in
        blip[-fade_samples:] *= fade_out
    return blip


def play_audio(
    audio: np.ndarray,
    sample_rate: int = config.SAMPLE_RATE,
    blocking: bool = True,
) -> None:
    """Play a numpy audio array through the default output device.

    Parameters
    ----------
    audio:
        1-D float32 audio samples.
    sample_rate:
        Playback sample rate.
    blocking:
        If ``True``, wait for playback to finish.
    """
    try:
        import sounddevice as sd  # noqa: WPS433

        sd.play(audio, samplerate=sample_rate)
        if blocking:
            sd.wait()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Audio playback failed: %s", exc)


def open_input_stream(
    callback,
    sample_rate: int = config.SAMPLE_RATE,
    channels: int = config.CHANNELS,
    chunk_samples: int = config.AUDIO_CHUNK_SAMPLES,
    dtype: str = "float32",
) -> "Optional[sounddevice.InputStream]":
    """Open a ``sounddevice.InputStream`` with the given callback.

    Parameters
    ----------
    callback:
        ``sounddevice``-compatible callback ``(indata, frames, time, status)``.
    sample_rate:
        Recording sample rate.
    channels:
        Number of channels.
    chunk_samples:
        Block size in samples.
    dtype:
        Sample data type.

    Returns
    -------
    sounddevice.InputStream or None
        The opened stream, or ``None`` if sound device is unavailable.
    """
    try:
        import sounddevice as sd  # noqa: WPS433

        stream = sd.InputStream(
            samplerate=sample_rate,
            channels=channels,
            blocksize=chunk_samples,
            dtype=dtype,
            callback=callback,
        )
        return stream
    except Exception as exc:  # noqa: BLE001
        logger.error("Cannot open input stream: %s", exc)
        return None
