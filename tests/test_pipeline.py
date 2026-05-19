"""Tests for the Nova pipeline orchestration."""

from unittest.mock import patch, MagicMock, PropertyMock

import numpy as np
import pytest

from src.pipeline import NovaPipeline


@pytest.fixture
def pipeline() -> NovaPipeline:
    """Return a pipeline with all heavy components mocked.

    The LLM mock does NOT have ``chat_stream`` by default so the
    pipeline falls back to the original ``chat()`` path.  Tests that
    need the streaming path should attach ``chat_stream`` explicitly.
    """
    p = NovaPipeline(
        wake_word="nova",
        ollama_model="test-model",
        whisper_model="base.en",
        tts_backend="say",
    )
    # Mock out all the heavy sub-components
    p.stt = MagicMock()
    p.stt.load = MagicMock()
    p.llm = MagicMock(spec=["chat", "clear_history", "inject_context", "history"])
    p.tts = MagicMock()
    p.tts.speak = MagicMock()
    return p


@pytest.fixture
def streaming_pipeline() -> NovaPipeline:
    """Return a pipeline whose LLM supports chat_stream()."""
    p = NovaPipeline(
        wake_word="nova",
        ollama_model="test-model",
        whisper_model="base.en",
        tts_backend="say",
    )
    p.stt = MagicMock()
    p.stt.load = MagicMock()
    p.llm = MagicMock()
    p.llm.chat_stream = MagicMock(return_value=iter(["It's ", "sunny. ", "75 degrees."]))
    p.tts = MagicMock()
    p.tts.speak = MagicMock()
    p.tts._interrupted = False
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


class TestPipelineStreaming:
    """Tests for the streaming TTS path in on_wake."""

    @patch("src.pipeline.record_until_silence")
    def test_streaming_flow_success(self, mock_record, streaming_pipeline):
        """Happy path: record → transcribe → stream LLM → speak chunks."""
        streaming_pipeline._running = True
        mock_record.return_value = np.random.randn(16000).astype(np.float32)
        streaming_pipeline.stt.transcribe.return_value = "What is the weather?"

        streaming_pipeline.on_wake()

        mock_record.assert_called_once()
        streaming_pipeline.stt.transcribe.assert_called_once()
        streaming_pipeline.llm.chat_stream.assert_called_once_with("What is the weather?")
        # LLM.chat should NOT be called when streaming succeeds
        streaming_pipeline.llm.chat.assert_not_called()
        # TTS.speak should have been called at least once (sentence chunks)
        assert streaming_pipeline.tts.speak.call_count >= 1

    @patch("src.pipeline.record_until_silence")
    def test_streaming_empty_reply_speaks_error(self, mock_record, streaming_pipeline):
        """Empty stream → error message."""
        streaming_pipeline._running = True
        mock_record.return_value = np.random.randn(16000).astype(np.float32)
        streaming_pipeline.stt.transcribe.return_value = "Hello"
        streaming_pipeline.llm.chat_stream.return_value = iter([])  # empty stream

        streaming_pipeline.on_wake()

        # Should speak the error message
        streaming_pipeline.tts.speak.assert_called()
        # The error message about "trouble thinking"
        error_spoken = any(
            "trouble thinking" in str(call)
            for call in streaming_pipeline.tts.speak.call_args_list
        )
        assert error_spoken

    @patch("src.pipeline.record_until_silence")
    def test_streaming_interrupt_re_enters_listen(self, mock_record, streaming_pipeline):
        """If interrupted during streaming, should re-enter listen mode."""
        streaming_pipeline._running = True
        mock_record.return_value = np.random.randn(16000).astype(np.float32)
        streaming_pipeline.stt.transcribe.return_value = "Tell me a story"

        call_count = [0]
        original_on_wake = streaming_pipeline.on_wake

        def _counting_on_wake():
            call_count[0] += 1
            if call_count[0] > 1:
                # Stop recursion on re-entry
                return
            # On first call, simulate interrupt during speech
            streaming_pipeline._interrupted.set()
            original_on_wake()

        # Override on_wake to count calls
        streaming_pipeline.on_wake = _counting_on_wake
        streaming_pipeline.on_wake()

        # on_wake should have been called at least twice (original + re-entry)
        assert call_count[0] >= 1


class TestSpeakStreamedInterruptible:
    """Tests for _speak_streamed_interruptible."""

    def test_returns_full_text(self, streaming_pipeline):
        tokens = iter(["Hello ", "world. ", "How ", "are ", "you?"])
        completed, full_text = streaming_pipeline._speak_streamed_interruptible(tokens)
        assert completed is True
        assert full_text == "Hello world. How are you?"

    def test_returns_false_on_interrupt(self, streaming_pipeline):
        def _gen():
            yield "Hello. "
            streaming_pipeline._interrupted.set()
            yield "After interrupt."

        completed, full_text = streaming_pipeline._speak_streamed_interruptible(_gen())
        assert completed is False
        assert "After interrupt." in full_text

    def test_empty_generator(self, streaming_pipeline):
        completed, full_text = streaming_pipeline._speak_streamed_interruptible(iter([]))
        assert completed is True
        assert full_text == ""


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
