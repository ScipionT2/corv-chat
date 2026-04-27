"""Tests for the voice command system."""

from datetime import datetime
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from src.commands import CommandResult, CommandResponse, parse_command


class TestClearHistory:
    """Tests for the clear history command."""

    @pytest.mark.parametrize(
        "text",
        [
            "Jarvis, clear history",
            "jarvis clear history",
            "Jarvis, reset conversation",
            "Jarvis, delete the history",
            "Jarvis, erase memory",
            "clear history",
            "reset the conversation",
            "erase chat",
            "delete memory",
        ],
    )
    def test_clear_history_variants(self, text: str) -> None:
        resp = parse_command(text)
        assert resp.result == CommandResult.CLEAR_HISTORY
        assert resp.message is not None

    def test_clear_history_has_message(self) -> None:
        resp = parse_command("Jarvis, clear history")
        assert "cleared" in resp.message.lower()


class TestTimeCommand:
    """Tests for the time command."""

    @pytest.mark.parametrize(
        "text",
        [
            "Jarvis, what time is it",
            "Jarvis, what's the time",
            "what time is it?",
            "what is the time right now?",
            "what is the current time",
            "Jarvis, what's the time?",
        ],
    )
    def test_time_variants(self, text: str) -> None:
        resp = parse_command(text)
        assert resp.result == CommandResult.HANDLED
        assert resp.message is not None
        assert "time" in resp.message.lower()

    def test_time_includes_actual_time(self) -> None:
        resp = parse_command("what time is it")
        now = datetime.now()
        hour = now.strftime("%-I")
        assert hour in resp.message


class TestStopListening:
    """Tests for the stop/pause command."""

    @pytest.mark.parametrize(
        "text",
        [
            "Jarvis, stop listening",
            "jarvis, pause",
            "stop listening",
            "pause listening",
            "go to sleep",
            "Jarvis, sleep",
        ],
    )
    def test_stop_variants(self, text: str) -> None:
        resp = parse_command(text)
        assert resp.result == CommandResult.PAUSE
        assert resp.message is not None

    def test_stop_has_message(self) -> None:
        resp = parse_command("Jarvis, stop listening")
        assert "sleep" in resp.message.lower()


class TestResume:
    """Tests for the resume/wake command."""

    @pytest.mark.parametrize(
        "text",
        [
            "Jarvis, resume",
            "resume listening",
            "wake up",
            "start listening",
            "I'm back",
            "unpause",
        ],
    )
    def test_resume_variants(self, text: str) -> None:
        resp = parse_command(text)
        assert resp.result == CommandResult.RESUME
        assert resp.message is not None


class TestNotACommand:
    """Tests for non-command inputs."""

    @pytest.mark.parametrize(
        "text",
        [
            "What's the weather today?",
            "Tell me a joke",
            "How do I install Python?",
            "Jarvis, tell me about history",
            "Clear my schedule",
            "",
            "   ",
        ],
    )
    def test_regular_speech_not_command(self, text: str) -> None:
        resp = parse_command(text)
        assert resp.result == CommandResult.NOT_A_COMMAND


class TestPipelineCommandIntegration:
    """Test that the pipeline dispatches commands correctly."""

    @patch("src.pipeline.record_until_silence")
    def test_clear_history_command_in_pipeline(self, mock_record) -> None:
        from src.pipeline import JarvisPipeline

        p = JarvisPipeline(
            wake_word="jarvis",
            ollama_model="test",
            whisper_model="base.en",
            tts_backend="say",
        )
        p.stt = MagicMock()
        p.llm = MagicMock()
        p.tts = MagicMock()
        p._running = True

        mock_record.return_value = np.random.randn(16000).astype(np.float32)
        p.stt.transcribe.return_value = "Jarvis, clear history"

        p.on_wake()

        p.llm.clear_history.assert_called_once()
        p.tts.speak.assert_called_once()
        assert "cleared" in p.tts.speak.call_args[0][0].lower()
        p.llm.chat.assert_not_called()

    @patch("src.pipeline.record_until_silence")
    def test_time_command_in_pipeline(self, mock_record) -> None:
        from src.pipeline import JarvisPipeline

        p = JarvisPipeline(
            wake_word="jarvis",
            ollama_model="test",
            whisper_model="base.en",
            tts_backend="say",
        )
        p.stt = MagicMock()
        p.llm = MagicMock()
        p.tts = MagicMock()
        p._running = True

        mock_record.return_value = np.random.randn(16000).astype(np.float32)
        p.stt.transcribe.return_value = "Jarvis, what time is it"

        p.on_wake()

        p.tts.speak.assert_called_once()
        assert "time" in p.tts.speak.call_args[0][0].lower()
        p.llm.chat.assert_not_called()

    @patch("src.pipeline.record_until_silence")
    def test_normal_speech_goes_to_llm(self, mock_record) -> None:
        from src.pipeline import JarvisPipeline

        p = JarvisPipeline(
            wake_word="jarvis",
            ollama_model="test",
            whisper_model="base.en",
            tts_backend="say",
        )
        p.stt = MagicMock()
        p.llm = MagicMock()
        p.llm.chat.return_value = "The weather is nice."
        p.tts = MagicMock()
        p._running = True

        mock_record.return_value = np.random.randn(16000).astype(np.float32)
        p.stt.transcribe.return_value = "What's the weather today?"

        p.on_wake()

        p.llm.chat.assert_called_once_with("What's the weather today?")
        p.tts.speak.assert_called_once_with("The weather is nice.")
