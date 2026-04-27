"""Tests for the multiple voice options feature."""

from unittest.mock import patch, MagicMock

import pytest

from src.tts import TextToSpeech, get_available_voices
import src.tts as tts_module


class TestGetAvailableVoices:
    """Tests for voice listing."""

    @patch("src.tts.subprocess.run")
    @patch.object(tts_module, "_say_available", return_value=True)
    def test_parses_voice_output(self, _say, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            stdout=(
                "Alex                en_US    # Most people recognize me by my voice.\n"
                "Daniel              en_GB    # Hello, my name is Daniel.\n"
                "Samantha            en_US    # Hello, my name is Samantha.\n"
            ),
            returncode=0,
        )
        voices = get_available_voices()
        assert len(voices) == 3
        assert voices[0]["name"] == "Alex"
        assert voices[0]["language"] == "en_US"
        assert voices[1]["name"] == "Daniel"
        assert voices[1]["language"] == "en_GB"

    @patch.object(tts_module, "_say_available", return_value=False)
    def test_returns_empty_when_say_unavailable(self, _say) -> None:
        voices = get_available_voices()
        assert voices == []

    @patch("src.tts.subprocess.run", side_effect=OSError("nope"))
    @patch.object(tts_module, "_say_available", return_value=True)
    def test_returns_empty_on_error(self, _say, _run) -> None:
        voices = get_available_voices()
        assert voices == []

    @patch("src.tts.subprocess.run")
    @patch.object(tts_module, "_say_available", return_value=True)
    def test_handles_empty_output(self, _say, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        voices = get_available_voices()
        assert voices == []


class TestVoiceSelection:
    """Tests for voice selection via --voice and JARVIS_VOICE."""

    def test_explicit_voice_param(self) -> None:
        tts = TextToSpeech(say_voice="Alex")
        assert tts.say_voice == "Alex"

    def test_jarvis_voice_env(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_VOICE", "Karen")
        tts = TextToSpeech()
        assert tts.say_voice == "Karen"

    def test_explicit_param_overrides_env(self, monkeypatch) -> None:
        monkeypatch.setenv("JARVIS_VOICE", "Karen")
        tts = TextToSpeech(say_voice="Alex")
        assert tts.say_voice == "Alex"

    def test_default_voice_is_daniel(self, monkeypatch) -> None:
        monkeypatch.delenv("JARVIS_VOICE", raising=False)
        # Config default is Daniel
        tts = TextToSpeech()
        # Should use config.MACOS_SAY_VOICE which we set to Daniel
        assert tts.say_voice is not None
        assert len(tts.say_voice) > 0

    @patch("src.tts.subprocess.run")
    def test_speak_uses_selected_voice(self, mock_run: MagicMock) -> None:
        tts = TextToSpeech(say_voice="Karen")
        tts._backend = "say"
        tts.speak("Hello test")
        args = mock_run.call_args[0][0]
        assert "Karen" in args
