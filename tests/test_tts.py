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

    @patch("src.tts.subprocess.run")
    @patch.object(tts_module, "_say_available", return_value=True)
    @patch.object(tts_module, "_check_piper", return_value=False)
    def test_speak_say(self, _piper, _say, mock_run):
        tts = TextToSpeech(backend="say", say_voice="Alex")
        tts._backend = "say"
        tts.speak("Hello world")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "say"
        assert "Hello world" in args

    @patch("src.tts.subprocess.run")
    def test_speak_say_voice_argument(self, mock_run):
        tts = TextToSpeech(say_voice="Samantha")
        tts._backend = "say"
        tts.speak("Test")
        args = mock_run.call_args[0][0]
        assert "-v" in args
        assert "Samantha" in args

    def test_speak_empty_text_does_nothing(self):
        tts = TextToSpeech()
        tts._backend = "say"
        # Should not raise
        tts.speak("")
        tts.speak("   ")

    @patch("src.tts.subprocess.run", side_effect=FileNotFoundError)
    def test_speak_say_missing_binary(self, mock_run):
        tts = TextToSpeech()
        tts._backend = "say"
        # Should not raise — just logs
        tts.speak("hello")

    @patch("src.tts.subprocess.run", side_effect=Exception("timeout"))
    def test_speak_say_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("say", 60)
        tts = TextToSpeech()
        tts._backend = "say"
        tts.speak("hello")  # should not raise

    def test_backend_property_caches(self):
        with patch.object(tts_module, "_select_backend", return_value="say") as mock_sel:
            tts = TextToSpeech(backend="auto")
            _ = tts.backend
            _ = tts.backend
            mock_sel.assert_called_once()
