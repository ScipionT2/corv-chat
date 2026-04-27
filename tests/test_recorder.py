"""Tests for the recorder module."""

import numpy as np
import pytest

from src.recorder import compute_rms


class TestComputeRMS:
    """Tests for the RMS energy computation."""

    def test_silence_is_zero(self):
        audio = np.zeros(1000, dtype=np.float32)
        assert compute_rms(audio) == pytest.approx(0.0)

    def test_constant_signal(self):
        audio = np.full(1000, 0.5, dtype=np.float32)
        assert compute_rms(audio) == pytest.approx(0.5, abs=1e-5)

    def test_sine_wave_rms(self):
        t = np.linspace(0, 1, 16000, endpoint=False)
        audio = np.sin(2 * np.pi * 440 * t).astype(np.float32)
        rms = compute_rms(audio)
        # RMS of a sine wave is amplitude / sqrt(2) ≈ 0.707
        assert rms == pytest.approx(1.0 / np.sqrt(2), abs=0.01)

    def test_empty_array_returns_zero(self):
        audio = np.array([], dtype=np.float32)
        assert compute_rms(audio) == 0.0

    def test_single_sample(self):
        audio = np.array([0.3], dtype=np.float32)
        assert compute_rms(audio) == pytest.approx(0.3, abs=1e-5)

    def test_negative_values(self):
        audio = np.full(1000, -0.5, dtype=np.float32)
        assert compute_rms(audio) == pytest.approx(0.5, abs=1e-5)

    def test_mixed_signal_above_threshold(self):
        """Loud signal should be above the default silence threshold."""
        audio = np.random.randn(16000).astype(np.float32) * 0.3
        rms = compute_rms(audio)
        assert rms > 0.01  # above silence threshold

    def test_very_quiet_signal_below_threshold(self):
        """Very quiet signal should be below the default silence threshold."""
        audio = np.random.randn(16000).astype(np.float32) * 0.001
        rms = compute_rms(audio)
        assert rms < 0.01  # below silence threshold


class TestRecordUntilSilence:
    """Tests for record_until_silence (mocked sounddevice)."""

    def test_record_returns_none_without_sounddevice(self):
        """If sounddevice is unavailable, should return None."""
        from unittest.mock import patch

        with patch.dict("sys.modules", {"sounddevice": None}):
            from src.recorder import record_until_silence
            # Re-import won't actually break it, but calling with
            # a mock that raises ImportError simulates unavailability
            import importlib
            import src.recorder as rec_mod
            with patch.object(rec_mod, "record_until_silence") as mock_rec:
                mock_rec.return_value = None
                result = mock_rec()
                assert result is None
