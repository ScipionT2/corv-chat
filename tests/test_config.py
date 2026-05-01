"""Tests for the config module."""

import os
import importlib


class TestConfigDefaults:
    """Verify that config defaults are sensible."""

    def test_wake_word_default(self):
        import config
        expected = os.environ.get("EP_WAKE_WORD") or os.environ.get("JARVIS_WAKE_WORD", "ep")
        assert config.WAKE_WORD == expected

    def test_sample_rate_default(self):
        import config
        assert config.SAMPLE_RATE == 16000 or "EP_SAMPLE_RATE" in os.environ or "JARVIS_SAMPLE_RATE" in os.environ

    def test_ollama_url_default(self):
        import config
        expected = os.environ.get("EP_OLLAMA_URL") or os.environ.get("JARVIS_OLLAMA_URL", "http://localhost:11434")
        assert config.OLLAMA_BASE_URL == expected

    def test_ollama_model_default(self):
        import config
        expected = os.environ.get("EP_OLLAMA_MODEL") or os.environ.get("JARVIS_OLLAMA_MODEL", "qwen2.5:1.5b")
        assert config.OLLAMA_MODEL == expected

    def test_whisper_model_default(self):
        import config
        expected = os.environ.get("EP_WHISPER_MODEL") or os.environ.get("JARVIS_WHISPER_MODEL", "base.en")
        assert config.WHISPER_MODEL == expected

    def test_silence_threshold_ms(self):
        import config
        assert config.SILENCE_THRESHOLD_MS >= 100

    def test_max_record_seconds(self):
        import config
        assert config.MAX_RECORD_SECONDS == 30 or "EP_MAX_RECORD_SEC" in os.environ or "JARVIS_MAX_RECORD_SEC" in os.environ

    def test_channels_mono(self):
        import config
        assert config.CHANNELS == 1

    def test_system_prompt_not_empty(self):
        import config
        assert len(config.LLM_SYSTEM_PROMPT) > 10

    def test_max_history_positive(self):
        import config
        assert config.LLM_MAX_HISTORY > 0


class TestConfigEnvOverride:
    """Verify that environment variables override defaults."""

    def test_env_override_wake_word_ep(self, monkeypatch):
        monkeypatch.setenv("EP_WAKE_WORD", "alexa")
        import config
        importlib.reload(config)
        assert config.WAKE_WORD == "alexa"

    def test_env_override_wake_word_legacy(self, monkeypatch):
        monkeypatch.setenv("JARVIS_WAKE_WORD", "alexa")
        import config
        importlib.reload(config)
        assert config.WAKE_WORD == "alexa"

    def test_env_override_ollama_model(self, monkeypatch):
        monkeypatch.setenv("EP_OLLAMA_MODEL", "llama3:8b")
        import config
        importlib.reload(config)
        assert config.OLLAMA_MODEL == "llama3:8b"

    def test_env_override_ollama_model_legacy(self, monkeypatch):
        monkeypatch.setenv("JARVIS_OLLAMA_MODEL", "llama3:8b")
        import config
        importlib.reload(config)
        assert config.OLLAMA_MODEL == "llama3:8b"

    def test_env_override_sample_rate(self, monkeypatch):
        monkeypatch.setenv("EP_SAMPLE_RATE", "44100")
        import config
        importlib.reload(config)
        assert config.SAMPLE_RATE == 44100

    def test_env_override_sample_rate_legacy(self, monkeypatch):
        monkeypatch.setenv("JARVIS_SAMPLE_RATE", "44100")
        import config
        importlib.reload(config)
        assert config.SAMPLE_RATE == 44100

    def test_env_override_invalid_int_uses_default(self, monkeypatch):
        monkeypatch.setenv("EP_SAMPLE_RATE", "not_a_number")
        import config
        importlib.reload(config)
        # Should fall back to default
        assert config.SAMPLE_RATE == 16000
