"""
Audio playback and utility helpers.

Provides thin wrappers around ``sounddevice`` for recording and playback,
plus activation/deactivation chime generators.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

import config

logger = logging.getLogger(__name__)


def generate_chime(
    frequency_hz: int = config.CHIME_FREQUENCY_HZ,
    duration_ms: int = config.CHIME_DURATION_MS,
    sample_rate: int = config.SAMPLE_RATE,
    amplitude: float = 0.25,
) -> np.ndarray:
    """Generate a calm, harmonically-rich activation chime.

    Uses a fundamental frequency with a softer octave harmonic layered on top,
    and a smooth fade-in/fade-out envelope to avoid harshness.

    Parameters
    ----------
    frequency_hz:
        Fundamental tone frequency in Hz (default ~480 Hz).
    duration_ms:
        Duration in milliseconds (default 200 ms).
    sample_rate:
        Audio sample rate.
    amplitude:
        Peak amplitude (0.0–1.0). Default 0.25 for a gentle sound.

    Returns
    -------
    np.ndarray
        1-D float32 array of audio samples.
    """
    num_samples = int(sample_rate * duration_ms / 1000)
    t = np.linspace(0, duration_ms / 1000.0, num_samples, endpoint=False)

    # Fundamental + octave harmonic at 30% volume for richness
    fundamental = np.sin(2 * np.pi * frequency_hz * t)
    harmonic = 0.3 * np.sin(2 * np.pi * (frequency_hz * 2) * t)
    chime = amplitude * (fundamental + harmonic) / 1.3  # Normalize

    # Smooth envelope: longer fade-in (30%) and fade-out (50%) for gentle attack/release
    fade_in_samples = int(num_samples * 0.3)
    fade_out_samples = int(num_samples * 0.5)

    if fade_in_samples > 0:
        # Raised cosine fade-in (smoother than linear)
        fade_in = 0.5 * (1 - np.cos(np.pi * np.linspace(0, 1, fade_in_samples)))
        chime[:fade_in_samples] *= fade_in

    if fade_out_samples > 0:
        # Raised cosine fade-out
        fade_out = 0.5 * (1 + np.cos(np.pi * np.linspace(0, 1, fade_out_samples)))
        chime[-fade_out_samples:] *= fade_out

    return chime.astype(np.float32)


def generate_deactivation_chime(
    frequency_hz: int = config.CHIME_FREQUENCY_HZ,
    duration_ms: int = 250,
    sample_rate: int = config.SAMPLE_RATE,
    amplitude: float = 0.2,
) -> np.ndarray:
    """Generate a gentle descending tone for deactivation/completion.

    A soft pitch-drop from the base frequency down a major third,
    with smooth envelope shaping.

    Parameters
    ----------
    frequency_hz:
        Starting frequency in Hz.
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
    num_samples = int(sample_rate * duration_ms / 1000)
    t = np.linspace(0, duration_ms / 1000.0, num_samples, endpoint=False)

    # Descending pitch: start at frequency_hz, end at frequency_hz * 0.8 (major third down)
    freq_start = frequency_hz
    freq_end = frequency_hz * 0.8
    freq_sweep = np.linspace(freq_start, freq_end, num_samples)

    # Instantaneous phase from frequency sweep
    phase = 2 * np.pi * np.cumsum(freq_sweep) / sample_rate
    fundamental = np.sin(phase)
    harmonic = 0.2 * np.sin(phase * 1.5)  # Fifth harmonic for warmth

    chime = amplitude * (fundamental + harmonic) / 1.2

    # Gentle envelope: short attack, long release
    fade_in_samples = int(num_samples * 0.15)
    fade_out_samples = int(num_samples * 0.6)

    if fade_in_samples > 0:
        fade_in = 0.5 * (1 - np.cos(np.pi * np.linspace(0, 1, fade_in_samples)))
        chime[:fade_in_samples] *= fade_in

    if fade_out_samples > 0:
        fade_out = 0.5 * (1 + np.cos(np.pi * np.linspace(0, 1, fade_out_samples)))
        chime[-fade_out_samples:] *= fade_out

    return chime.astype(np.float32)


# Backward-compatible alias — existing code imports generate_blip
def generate_blip(
    frequency_hz: int = config.BLIP_FREQUENCY_HZ,
    duration_ms: int = config.BLIP_DURATION_MS,
    sample_rate: int = config.SAMPLE_RATE,
    amplitude: float = 0.4,
) -> np.ndarray:
    """Backward-compatible alias that now delegates to generate_chime.

    Parameters are accepted for API compat but the output uses the new
    calm chime algorithm with the caller's amplitude/duration.

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
    return generate_chime(
        frequency_hz=frequency_hz,
        duration_ms=duration_ms,
        sample_rate=sample_rate,
        amplitude=amplitude,
    )


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
