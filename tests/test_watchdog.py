"""Tests for crash recovery, watchdog, and Ollama health monitoring."""

import threading
import time
from collections import deque
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest

from src.pipeline import NovaPipeline


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def pipeline() -> NovaPipeline:
    """Return a pipeline with all heavy components mocked.

    The LLM mock uses a spec that excludes ``chat_stream`` so the
    pipeline falls back to the non-streaming ``chat()`` path.
    """
    p = NovaPipeline(
        wake_word="nova",
        ollama_model="test-model",
        whisper_model="base.en",
        tts_backend="say",
    )
    p.stt = MagicMock()
    p.stt.load = MagicMock()
    p.llm = MagicMock(spec=["chat", "clear_history", "inject_context", "history"])
    p.tts = MagicMock()
    p.tts.speak = MagicMock()
    p.tts.stop = MagicMock()
    return p


# ==================================================================
# on_wake error handling
# ==================================================================

class TestOnWakeErrorHandling:
    """on_wake should catch exceptions and keep the pipeline alive."""

    @patch("src.pipeline.record_until_silence")
    def test_on_wake_catches_exception(self, mock_record, pipeline):
        """If _on_wake_inner raises, pipeline stays running."""
        pipeline._running = True
        mock_record.side_effect = RuntimeError("Audio device exploded")

        # Should NOT raise
        pipeline.on_wake()

        # Pipeline should still be running
        assert pipeline._running is True
        # Should have spoken an error
        pipeline.tts.speak.assert_called_once()
        assert "something went wrong" in pipeline.tts.speak.call_args[0][0]

    @patch("src.pipeline.record_until_silence")
    def test_on_wake_catches_stt_crash(self, mock_record, pipeline):
        """If STT crashes mid-flow, pipeline survives."""
        pipeline._running = True
        mock_record.return_value = np.random.randn(16000).astype(np.float32)
        pipeline.stt.transcribe.side_effect = Exception("Whisper segfault")

        pipeline.on_wake()

        assert pipeline._running is True
        pipeline.tts.speak.assert_called_once()
        assert "something went wrong" in pipeline.tts.speak.call_args[0][0]

    @patch("src.pipeline.record_until_silence")
    def test_on_wake_catches_llm_crash(self, mock_record, pipeline):
        """If LLM crashes, pipeline survives."""
        pipeline._running = True
        mock_record.return_value = np.random.randn(16000).astype(np.float32)
        pipeline.stt.transcribe.return_value = "Hello"
        # Must remove chat_stream so the non-streaming path is taken,
        # otherwise MagicMock auto-creates the attribute and bypasses chat()
        del pipeline.llm.chat_stream
        pipeline.llm.chat.side_effect = ConnectionError("Ollama died")

        pipeline.on_wake()

        assert pipeline._running is True
        pipeline.tts.speak.assert_called_once()
        assert "something went wrong" in pipeline.tts.speak.call_args[0][0]

    @patch("src.pipeline.record_until_silence")
    def test_normal_flow_still_works(self, mock_record, pipeline):
        """Happy path should still work with the error wrapper."""
        pipeline._running = True
        mock_record.return_value = np.random.randn(16000).astype(np.float32)
        pipeline.stt.transcribe.return_value = "What is the weather?"
        # Remove chat_stream so we go through the non-streaming path
        del pipeline.llm.chat_stream
        pipeline.llm.chat.return_value = "It's sunny."

        pipeline.on_wake()

        pipeline.llm.chat.assert_called_once_with("What is the weather?")
        pipeline.tts.speak.assert_called_once_with("It's sunny.")


# ==================================================================
# Ollama health check
# ==================================================================

class TestOllamaHealthCheck:
    """Tests for _check_ollama_health()."""

    @patch("src.pipeline.requests.get")
    def test_healthy_when_ollama_responds(self, mock_get, pipeline):
        """Returns True when Ollama responds with 200."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        result = pipeline._check_ollama_health()

        assert result is True
        assert pipeline._ollama_healthy is True

    @patch("src.pipeline.requests.get")
    def test_unhealthy_when_ollama_500(self, mock_get, pipeline):
        """Returns False when Ollama responds with 500."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        result = pipeline._check_ollama_health()

        assert result is False
        assert pipeline._ollama_healthy is False

    @patch("src.pipeline.requests.get")
    def test_unhealthy_when_connection_refused(self, mock_get, pipeline):
        """Returns False when Ollama is down entirely."""
        mock_get.side_effect = ConnectionError("Connection refused")

        result = pipeline._check_ollama_health()

        assert result is False
        assert pipeline._ollama_healthy is False

    @patch("src.pipeline.requests.get")
    def test_unhealthy_when_timeout(self, mock_get, pipeline):
        """Returns False when Ollama times out."""
        import requests as req
        mock_get.side_effect = req.exceptions.Timeout("timed out")

        result = pipeline._check_ollama_health()

        assert result is False
        assert pipeline._ollama_healthy is False

    @patch("src.pipeline.requests.get")
    def test_recovery_detected(self, mock_get, pipeline):
        """Detects when Ollama comes back after being down."""
        # Start unhealthy
        pipeline._ollama_healthy = False

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        result = pipeline._check_ollama_health()

        assert result is True
        assert pipeline._ollama_healthy is True


# ==================================================================
# Restart budget
# ==================================================================

class TestRestartBudget:
    """Tests for _can_restart() cooldown logic."""

    def test_first_restart_allowed(self, pipeline):
        """First restart should always be allowed."""
        assert pipeline._can_restart() is True

    def test_within_budget(self, pipeline):
        """Multiple restarts within budget are allowed."""
        for _ in range(4):
            assert pipeline._can_restart() is True

    @patch("config.MAX_RESTART_ATTEMPTS", 3)
    @patch("config.RESTART_COOLDOWN", 30)
    def test_exceeds_budget(self, pipeline):
        """Exceeding MAX_RESTART_ATTEMPTS should block further restarts."""
        # Fill up the budget
        for _ in range(3):
            pipeline._can_restart()

        # Next should be blocked
        assert pipeline._can_restart() is False

    @patch("config.MAX_RESTART_ATTEMPTS", 2)
    @patch("config.RESTART_COOLDOWN", 0.1)
    def test_budget_resets_after_cooldown(self, pipeline):
        """Budget resets after cooldown window expires."""
        pipeline._can_restart()
        pipeline._can_restart()
        # Budget exhausted
        assert pipeline._can_restart() is False

        # Wait for cooldown
        time.sleep(0.15)

        # Should be allowed again
        assert pipeline._can_restart() is True


# ==================================================================
# Watchdog thread
# ==================================================================

class TestWatchdog:
    """Tests for the watchdog thread."""

    @patch("src.pipeline.WakeWordDetector")
    @patch("config.WATCHDOG_ENABLED", True)
    @patch("config.WATCHDOG_INTERVAL", 1)
    def test_watchdog_starts_with_pipeline(self, MockDetector, pipeline):
        """Watchdog thread should start when pipeline starts."""
        mock_det = MagicMock()
        mock_det._running = True
        MockDetector.return_value = mock_det

        pipeline.start()

        assert pipeline._watchdog_thread is not None
        assert pipeline._watchdog_thread.is_alive()

        pipeline.stop()

    @patch("src.pipeline.WakeWordDetector")
    @patch("config.WATCHDOG_ENABLED", False)
    def test_watchdog_disabled(self, MockDetector, pipeline):
        """Watchdog should not start when disabled."""
        mock_det = MagicMock()
        MockDetector.return_value = mock_det

        pipeline.start()

        assert pipeline._watchdog_thread is None

        pipeline.stop()

    @patch("src.pipeline.WakeWordDetector")
    @patch("src.pipeline.requests.get")
    @patch("config.WATCHDOG_ENABLED", True)
    @patch("config.WATCHDOG_INTERVAL", 0.1)
    def test_watchdog_detects_dead_detector_and_restarts(
        self, mock_get, MockDetector, pipeline
    ):
        """Watchdog should restart a dead wake-word detector."""
        # Mock Ollama health check
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        # First detector (created by start())
        initial_det = MagicMock()
        initial_det._running = True  # initially healthy

        # Replacement detector
        replacement_det = MagicMock()
        replacement_det._running = True

        MockDetector.side_effect = [initial_det, replacement_det]

        pipeline.start()
        time.sleep(0.05)  # let watchdog start

        # Simulate detector death
        initial_det._running = False

        # Wait for watchdog to detect and restart
        time.sleep(0.5)

        # Should have created a new detector
        assert MockDetector.call_count >= 2
        replacement_det.start.assert_called()

        pipeline.stop()


# ==================================================================
# Config values
# ==================================================================

class TestWatchdogConfig:
    """Verify watchdog config defaults are loaded."""

    def test_watchdog_defaults(self):
        import config as cfg
        assert cfg.WATCHDOG_ENABLED is True
        assert cfg.WATCHDOG_INTERVAL == 5
        assert cfg.MAX_RESTART_ATTEMPTS == 5
        assert cfg.RESTART_COOLDOWN == 30
