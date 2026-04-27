"""Tests for the Speech-to-Text module."""

from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from src.stt import SpeechToText


@pytest.fixture
def stt() -> SpeechToText:
    """Return a SpeechToText instance (model not loaded)."""
    return SpeechToText(model_name="base.en", device="cpu", compute_type="int8")


class TestSpeechToText:
    """Tests for SpeechToText."""

    @patch("src.stt.SpeechToText.load")
    def test_transcribe_calls_model(self, mock_load, stt):
        """Transcribe should use the model and return text."""
        mock_segment = MagicMock()
        mock_segment.text = "Hello world"
        mock_info = MagicMock()

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_segment], mock_info)
        stt._model = mock_model

        audio = np.random.randn(16000).astype(np.float32)
        result = stt.transcribe(audio)
        assert result == "Hello world"

    @patch("src.stt.SpeechToText.load")
    def test_transcribe_multiple_segments(self, mock_load, stt):
        seg1 = MagicMock()
        seg1.text = "Hello"
        seg2 = MagicMock()
        seg2.text = "world"
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([seg1, seg2], MagicMock())
        stt._model = mock_model

        audio = np.random.randn(16000).astype(np.float32)
        result = stt.transcribe(audio)
        assert result == "Hello world"

    def test_transcribe_empty_audio_returns_none(self, stt):
        stt._model = MagicMock()
        result = stt.transcribe(np.array([], dtype=np.float32))
        assert result is None

    def test_transcribe_none_audio_returns_none(self, stt):
        stt._model = MagicMock()
        result = stt.transcribe(None)
        assert result is None

    @patch("src.stt.SpeechToText.load")
    def test_transcribe_empty_result_returns_none(self, mock_load, stt):
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([], MagicMock())
        stt._model = mock_model

        audio = np.random.randn(16000).astype(np.float32)
        result = stt.transcribe(audio)
        assert result is None

    @patch("src.stt.SpeechToText.load")
    def test_transcribe_exception_returns_none(self, mock_load, stt):
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError("model error")
        stt._model = mock_model

        audio = np.random.randn(16000).astype(np.float32)
        result = stt.transcribe(audio)
        assert result is None

    @patch("src.stt.SpeechToText.load")
    def test_transcribe_whitespace_only_returns_none(self, mock_load, stt):
        mock_segment = MagicMock()
        mock_segment.text = "   "
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_segment], MagicMock())
        stt._model = mock_model

        audio = np.random.randn(16000).astype(np.float32)
        result = stt.transcribe(audio)
        assert result is None

    def test_load_is_noop_when_model_exists(self, stt):
        sentinel = MagicMock()
        stt._model = sentinel
        stt.load()  # should not replace the model
        assert stt._model is sentinel
