"""Tests for the Jarvis pipeline orchestration."""

from unittest.mock import patch, MagicMock, PropertyMock

import numpy as np
import pytest

from src.pipeline import JarvisPipeline


@pytest.fixture
def pipeline() -> JarvisPipeline:
    """Return a pipeline with all heavy components mocked."""
    p = JarvisPipeline(
        wake_word="jarvis",
        ollama_model="test-model",
        whisper_model="base.en",
        tts_backend="say",
    )
    # Mock out all the heavy sub-components
    p.stt = MagicMock()
    p.stt.load = MagicMock()
    p.llm = MagicMock()
    p.tts = MagicMock()
    p.tts.speak = MagicMock()
    return p


class TestPipelineOnWake:
    """Tests for the on_wake interaction flow."""

    @patch("src.pipeline.record_until_silence")
    def test_full_flow_success(self, mock_record, pipeline):
        """Happy path: record → transcribe → LLM → speak."""
        pipeline._running = True
        mock_record.return_value = np.random.randn(16000).astype(np.float32)
        pipeline.stt.transcribe.return_value = "What is the weather?"
        pipeline.llm.chat.return_value = "It's sunny and 75 degrees."

        pipeline.on_wake()

        mock_record.assert_called_once()
        pipeline.stt.transcribe.assert_called_once()
        pipeline.llm.chat.assert_called_once_with("What is the weather?")
        pipeline.tts.speak.assert_called_once_with("It's sunny and 75 degrees.")

    @patch("src.pipeline.record_until_silence")
    def test_no_audio_speaks_error(self, mock_record, pipeline):
        """If recording fails, speak an error."""
        pipeline._running = True
        mock_record.return_value = None

        pipeline.on_wake()

        pipeline.tts.speak.assert_called_once()
        assert "didn't catch" in pipeline.tts.speak.call_args[0][0]
        pipeline.stt.transcribe.assert_not_called()

    @patch("src.pipeline.record_until_silence")
    def test_empty_audio_speaks_error(self, mock_record, pipeline):
        pipeline._running = True
        mock_record.return_value = np.array([], dtype=np.float32)

        pipeline.on_wake()

        pipeline.tts.speak.assert_called_once()
        assert "didn't catch" in pipeline.tts.speak.call_args[0][0]

    @patch("src.pipeline.record_until_silence")
    def test_transcription_fails_speaks_error(self, mock_record, pipeline):
        pipeline._running = True
        mock_record.return_value = np.random.randn(16000).astype(np.float32)
        pipeline.stt.transcribe.return_value = None

        pipeline.on_wake()

        pipeline.tts.speak.assert_called_once()
        assert "couldn't understand" in pipeline.tts.speak.call_args[0][0]
        pipeline.llm.chat.assert_not_called()

    @patch("src.pipeline.record_until_silence")
    def test_llm_fails_speaks_error(self, mock_record, pipeline):
        pipeline._running = True
        mock_record.return_value = np.random.randn(16000).astype(np.float32)
        pipeline.stt.transcribe.return_value = "Hello"
        pipeline.llm.chat.return_value = None

        pipeline.on_wake()

        pipeline.tts.speak.assert_called_once()
        assert "trouble thinking" in pipeline.tts.speak.call_args[0][0]

    @patch("src.pipeline.record_until_silence")
    def test_not_running_does_nothing(self, mock_record, pipeline):
        """If pipeline isn't running, on_wake should be a no-op."""
        pipeline._running = False

        pipeline.on_wake()

        mock_record.assert_not_called()
        pipeline.stt.transcribe.assert_not_called()
        pipeline.llm.chat.assert_not_called()
        pipeline.tts.speak.assert_not_called()


class TestPipelineLifecycle:
    """Tests for start/stop lifecycle."""

    @patch("src.pipeline.WakeWordDetector")
    def test_start_loads_stt_and_starts_detector(self, MockDetector, pipeline):
        mock_det = MagicMock()
        MockDetector.return_value = mock_det

        pipeline.start()

        pipeline.stt.load.assert_called_once()
        mock_det.start.assert_called_once()
        assert pipeline._running is True

    @patch("src.pipeline.WakeWordDetector")
    def test_stop_sets_running_false(self, MockDetector, pipeline):
        mock_det = MagicMock()
        MockDetector.return_value = mock_det
        pipeline.start()

        pipeline.stop()

        assert pipeline._running is False
        mock_det.stop.assert_called_once()

    @patch("src.pipeline.WakeWordDetector")
    def test_stop_without_start_is_safe(self, MockDetector, pipeline):
        """Calling stop before start should not raise."""
        pipeline.stop()
        assert pipeline._running is False
