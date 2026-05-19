"""Tests for the Text-to-Speech module."""

from unittest.mock import patch, MagicMock

import pytest

from src.tts import TextToSpeech, _check_piper, _say_available, _select_backend
import src.tts as tts_module


class TestBackendDetection:
    """Tests for backend selection logic."""

    def test_select_backend_piper_when_available(self):
        with patch.object(tts_module, "_check_piper", return_value=True):
            assert _select_backend("piper") == "piper"

    def test_select_backend_piper_when_unavailable_raises(self):
        with patch.object(tts_module, "_check_piper", return_value=False):
            with pytest.raises(RuntimeError, match="piper"):
                _select_backend("piper")

    def test_select_backend_say_when_available(self):
        with patch.object(tts_module, "_say_available", return_value=True):
            assert _select_backend("say") == "say"

    def test_select_backend_say_when_unavailable_raises(self):
        with patch.object(tts_module, "_say_available", return_value=False):
            with pytest.raises(RuntimeError, match="say"):
                _select_backend("say")

    def test_auto_prefers_piper(self):
        with patch.object(tts_module, "_check_piper", return_value=True):
            assert _select_backend("auto") == "piper"

    def test_auto_falls_back_to_say(self):
        with patch.object(tts_module, "_check_piper", return_value=False):
            with patch.object(tts_module, "_say_available", return_value=True):
                assert _select_backend("auto") == "say"

    def test_auto_no_backend_raises(self):
        with patch.object(tts_module, "_check_piper", return_value=False):
            with patch.object(tts_module, "_say_available", return_value=False):
                with pytest.raises(RuntimeError, match="No TTS backend"):
                    _select_backend("auto")


class TestTextToSpeech:
    """Tests for the TextToSpeech class."""

    @patch("src.tts.subprocess.Popen")
    @patch.object(tts_module, "_say_available", return_value=True)
    @patch.object(tts_module, "_check_piper", return_value=False)
    def test_speak_say(self, _piper, _say, mock_popen):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc
        tts = TextToSpeech(backend="say", say_voice="Alex")
        tts._backend = "say"
        tts.speak("Hello world")
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert args[0] == "say"
        assert "Hello world" in args

    @patch("src.tts.subprocess.Popen")
    def test_speak_say_voice_argument(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc
        tts = TextToSpeech(say_voice="Samantha")
        tts._backend = "say"
        tts.speak("Test")
        args = mock_popen.call_args[0][0]
        assert "-v" in args
        assert "Samantha" in args

    def test_speak_empty_text_does_nothing(self):
        tts = TextToSpeech()
        tts._backend = "say"
        # Should not raise
        tts.speak("")
        tts.speak("   ")

    @patch("src.tts.subprocess.Popen", side_effect=FileNotFoundError)
    def test_speak_say_missing_binary(self, mock_popen):
        tts = TextToSpeech()
        tts._backend = "say"
        # Should not raise — just logs
        tts.speak("hello")

    @patch("src.tts.subprocess.Popen")
    def test_speak_say_timeout(self, mock_popen):
        import subprocess
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = subprocess.TimeoutExpired("say", 60)
        mock_proc.kill.return_value = None
        mock_popen.return_value = mock_proc
        tts = TextToSpeech()
        tts._backend = "say"
        tts.speak("hello")  # should not raise

    def test_backend_property_caches(self):
        with patch.object(tts_module, "_select_backend", return_value="say") as mock_sel:
            tts = TextToSpeech(backend="auto")
            _ = tts.backend
            _ = tts.backend
            mock_sel.assert_called_once()


class TestSpeakStreamed:
    """Tests for the speak_streamed() method."""

    @patch("src.tts.subprocess.Popen")
    def test_speak_streamed_buffers_sentences(self, mock_popen):
        """Tokens are buffered until sentence boundary, then spoken."""
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.poll.return_value = 0
        mock_popen.return_value = mock_proc

        tts = TextToSpeech(say_voice="Alex")
        tts._backend = "say"

        tokens = ["Hello ", "world. ", "How ", "are ", "you?"]
        result = tts.speak_streamed(iter(tokens))

        assert result is True
        # Should have spoken at least 2 chunks (sentence boundary at ". ")
        assert mock_popen.call_count >= 2

    @patch("src.tts.subprocess.Popen")
    def test_speak_streamed_returns_true_on_completion(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.poll.return_value = 0
        mock_popen.return_value = mock_proc

        tts = TextToSpeech(say_voice="Alex")
        tts._backend = "say"

        result = tts.speak_streamed(iter(["Hello."]))
        assert result is True

    @patch("src.tts.subprocess.Popen")
    def test_speak_streamed_returns_false_on_interrupt(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.poll.return_value = 0
        mock_popen.return_value = mock_proc

        tts = TextToSpeech(say_voice="Alex")
        tts._backend = "say"

        def _interrupting_gen():
            yield "Hello world. "
            tts._interrupted = True
            yield "This should not be spoken. "

        result = tts.speak_streamed(_interrupting_gen())
        assert result is False

    @patch("src.tts.subprocess.Popen")
    def test_speak_streamed_empty_generator(self, mock_popen):
        tts = TextToSpeech(say_voice="Alex")
        tts._backend = "say"

        result = tts.speak_streamed(iter([]))
        assert result is True
        mock_popen.assert_not_called()

    @patch("src.tts.subprocess.Popen")
    def test_speak_streamed_long_buffer_flushes(self, mock_popen):
        """Buffers > 200 chars should flush even without sentence boundary."""
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.poll.return_value = 0
        mock_popen.return_value = mock_proc

        tts = TextToSpeech(say_voice="Alex")
        tts._backend = "say"

        # Single long token with no sentence boundary
        long_text = "a" * 250
        result = tts.speak_streamed(iter([long_text]))
        assert result is True
        assert mock_popen.call_count >= 1
