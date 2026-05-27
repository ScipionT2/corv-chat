"""Tests for the wake-word detection module (multi-backend).

Updated 2026-05-26: Added tests for VoskKeywordDetector and PorcupineDetector.
Backend selection now defaults to 'vosk' for 'nova' (lighter than Whisper).
"""

from __future__ import annotations

import importlib
import json
import os
import re
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest

import config
from src.wake_word import (
    KeywordDetector,
    OpenWakeWordDetector,
    PorcupineDetector,
    VoskKeywordDetector,
    WakeWordDetector,
    resolve_wake_word,
    _select_backend,
    _OPENWAKEWORD_ALIASES,
    _KEYWORD_WAKE_WORDS,
    _PORCUPINE_BUILTIN,
)


# =========================================================================
# resolve_wake_word
# =========================================================================

class TestResolveWakeWord:
    def test_jarvis_resolves(self):
        assert resolve_wake_word("jarvis") == "hey_jarvis_v0.1"

    def test_hey_jarvis_resolves(self):
        assert resolve_wake_word("hey jarvis") == "hey_jarvis_v0.1"

    def test_rhasspy_resolves(self):
        assert resolve_wake_word("rhasspy") == "hey_rhasspy_v0.1"

    def test_nova_does_not_resolve_to_jarvis(self):
        """'nova' should NOT map to an OpenWakeWord model — it uses keyword detection."""
        result = resolve_wake_word("nova")
        assert result == "nova"

    def test_unknown_word_passes_through(self):
        assert resolve_wake_word("alexa") == "alexa"

    def test_case_insensitive(self):
        assert resolve_wake_word("Jarvis") == "hey_jarvis_v0.1"
        assert resolve_wake_word("HEY JARVIS") == "hey_jarvis_v0.1"

    def test_strips_whitespace(self):
        assert resolve_wake_word("  jarvis  ") == "hey_jarvis_v0.1"


# =========================================================================
# _select_backend
# =========================================================================

class TestSelectBackend:
    def test_auto_nova_uses_vosk(self):
        """'nova' in auto mode should use the lightweight Vosk backend."""
        assert _select_backend("nova", "auto") == "vosk"

    def test_auto_hey_nova_uses_vosk(self):
        assert _select_backend("hey nova", "auto") == "vosk"

    def test_auto_jarvis_uses_openwakeword(self):
        assert _select_backend("jarvis", "auto") == "openwakeword"

    def test_auto_hey_jarvis_uses_openwakeword(self):
        assert _select_backend("hey jarvis", "auto") == "openwakeword"

    def test_forced_keyword(self):
        assert _select_backend("jarvis", "keyword") == "keyword"

    def test_forced_openwakeword(self):
        assert _select_backend("nova", "openwakeword") == "openwakeword"

    def test_forced_vosk(self):
        assert _select_backend("nova", "vosk") == "vosk"

    def test_forced_porcupine(self):
        assert _select_backend("nova", "porcupine") == "porcupine"

    def test_unknown_word_auto_falls_to_vosk(self):
        """Unknown words without OWW models should fall back to Vosk."""
        assert _select_backend("alexa", "auto") == "vosk"

    def test_case_insensitive(self):
        assert _select_backend("Nova", "auto") == "vosk"
        assert _select_backend("JARVIS", "auto") == "openwakeword"

    def test_ep_uses_vosk(self):
        assert _select_backend("ep", "auto") == "vosk"

    def test_hey_ep_uses_vosk(self):
        assert _select_backend("hey ep", "auto") == "vosk"

    def test_porcupine_auto_with_key_and_builtin(self):
        """With a Porcupine key set, built-in words should route to Porcupine."""
        with patch.object(config, "PORCUPINE_ACCESS_KEY", "test-key"):
            assert _select_backend("jarvis", "auto") == "porcupine"
            assert _select_backend("computer", "auto") == "porcupine"

    def test_porcupine_auto_with_key_and_ppn(self):
        """With a Porcupine key + custom .ppn, 'nova' should route to Porcupine."""
        with patch.object(config, "PORCUPINE_ACCESS_KEY", "test-key"), \
             patch.object(config, "PORCUPINE_MODEL_PATH", "/path/to/nova.ppn"):
            assert _select_backend("nova", "auto") == "porcupine"


# =========================================================================
# VoskKeywordDetector
# =========================================================================

class TestVoskKeywordDetectorMatchLogic:
    """Unit-test the Vosk keyword matching logic."""

    def _make_detector(self, keyword: str = "nova") -> VoskKeywordDetector:
        det = VoskKeywordDetector.__new__(VoskKeywordDetector)
        det.keyword = keyword.lower().strip()
        det._match_tokens = VoskKeywordDetector._build_match_tokens(det.keyword)
        return det

    def test_exact_match(self):
        det = self._make_detector("nova")
        assert det._matches("nova")

    def test_hey_prefix_match(self):
        det = self._make_detector("nova")
        assert det._matches("hey nova")

    def test_embedded_match(self):
        det = self._make_detector("nova")
        assert det._matches("nova please help")

    def test_case_insensitive(self):
        det = self._make_detector("nova")
        assert det._matches("NOVA")
        assert det._matches("Hey Nova")

    def test_no_match_on_random_speech(self):
        det = self._make_detector("nova")
        assert not det._matches("the weather is nice")
        assert not det._matches("hello world")

    def test_no_match_on_unk(self):
        det = self._make_detector("nova")
        assert not det._matches("[unk]")

    def test_no_match_on_empty(self):
        det = self._make_detector("nova")
        assert not det._matches("")

    def test_match_tokens_single_word(self):
        det = self._make_detector("nova")
        assert det._match_tokens == ["nova", "hey nova"]

    def test_match_tokens_phrase(self):
        det = self._make_detector("hey nova")
        # Phrases don't get the "hey <word>" variant added
        assert det._match_tokens == ["hey nova"]


class TestVoskKeywordDetectorLifecycle:
    """Test start/stop with mocked Vosk model and audio."""

    @patch("src.wake_word.open_input_stream")
    @patch("src.wake_word.VoskKeywordDetector._load_model")
    def test_start_opens_stream(self, mock_load, mock_stream):
        mock_s = MagicMock()
        mock_stream.return_value = mock_s
        cb = MagicMock()
        det = VoskKeywordDetector(keyword="nova", on_wake=cb)
        det.start()
        mock_load.assert_called_once()
        mock_stream.assert_called_once()
        mock_s.start.assert_called_once()
        assert det._running is True
        det.stop()

    @patch("src.wake_word.open_input_stream")
    @patch("src.wake_word.VoskKeywordDetector._load_model")
    def test_stop_cleans_up(self, mock_load, mock_stream):
        mock_s = MagicMock()
        mock_stream.return_value = mock_s
        cb = MagicMock()
        det = VoskKeywordDetector(keyword="nova", on_wake=cb)
        det.start()
        det.stop()
        assert det._running is False
        mock_s.stop.assert_called_once()
        mock_s.close.assert_called_once()

    @patch("src.wake_word.open_input_stream")
    @patch("src.wake_word.VoskKeywordDetector._load_model")
    def test_pause_resume(self, mock_load, mock_stream):
        mock_stream.return_value = MagicMock()
        cb = MagicMock()
        det = VoskKeywordDetector(keyword="nova", on_wake=cb)
        det.start()
        det.pause()
        assert det._paused is True
        det.resume()
        assert det._paused is False
        det.stop()

    @patch("src.wake_word.open_input_stream")
    @patch("src.wake_word.VoskKeywordDetector._load_model")
    def test_start_fails_without_stream(self, mock_load, mock_stream):
        mock_stream.return_value = None
        cb = MagicMock()
        det = VoskKeywordDetector(keyword="nova", on_wake=cb)
        with pytest.raises(RuntimeError, match="Cannot open microphone"):
            det.start()


class TestVoskKeywordDetectorAudioCallback:
    """Test the Vosk audio callback with a mocked recognizer."""

    def _make_detector_with_recognizer(self, keyword="nova"):
        det = VoskKeywordDetector.__new__(VoskKeywordDetector)
        det.keyword = keyword.lower().strip()
        det._match_tokens = VoskKeywordDetector._build_match_tokens(det.keyword)
        det.on_wake = MagicMock()
        det._running = True
        det._paused = False
        det._stream = MagicMock()
        det._recognizer = MagicMock()
        det._lock = threading.Lock()
        det._last_detection_time = 0.0
        return det

    def test_callback_skips_when_paused(self):
        det = self._make_detector_with_recognizer()
        det._paused = True
        indata = np.random.randn(1280, 1).astype(np.float32)
        det._audio_callback(indata, 1280, None, None)
        det._recognizer.AcceptWaveform.assert_not_called()

    def test_callback_skips_when_not_running(self):
        det = self._make_detector_with_recognizer()
        det._running = False
        indata = np.random.randn(1280, 1).astype(np.float32)
        det._audio_callback(indata, 1280, None, None)
        det._recognizer.AcceptWaveform.assert_not_called()

    @patch("src.wake_word.threading.Thread")
    def test_full_result_triggers_wake(self, mock_thread_cls):
        """Full Vosk result containing 'nova' should trigger wake."""
        det = self._make_detector_with_recognizer()
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        # Mock recognizer returning a full result with "nova"
        det._recognizer.AcceptWaveform.return_value = True
        det._recognizer.Result.return_value = json.dumps({"text": "nova"})

        indata = np.random.randn(1280, 1).astype(np.float32)
        det._audio_callback(indata, 1280, None, None)

        # Should have spawned a wake thread
        mock_thread_cls.assert_called_once()
        mock_thread.start.assert_called_once()
        assert det._paused is True

    @patch("src.wake_word.threading.Thread")
    def test_partial_result_triggers_wake(self, mock_thread_cls):
        """Partial Vosk result containing 'nova' should trigger wake (faster response)."""
        det = self._make_detector_with_recognizer()
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        # No full result, but partial contains "nova"
        det._recognizer.AcceptWaveform.return_value = False
        det._recognizer.PartialResult.return_value = json.dumps({"partial": "nova"})

        indata = np.random.randn(1280, 1).astype(np.float32)
        det._audio_callback(indata, 1280, None, None)

        mock_thread_cls.assert_called_once()
        mock_thread.start.assert_called_once()
        assert det._paused is True

    def test_no_match_no_wake(self):
        """Non-matching Vosk output should not trigger wake."""
        det = self._make_detector_with_recognizer()

        det._recognizer.AcceptWaveform.return_value = True
        det._recognizer.Result.return_value = json.dumps({"text": ""})

        indata = np.random.randn(1280, 1).astype(np.float32)
        det._audio_callback(indata, 1280, None, None)

        # on_wake should not have been called
        det.on_wake.assert_not_called()

    def test_unk_result_no_wake(self):
        """[unk] results from Vosk should not trigger wake."""
        det = self._make_detector_with_recognizer()

        det._recognizer.AcceptWaveform.return_value = True
        det._recognizer.Result.return_value = json.dumps({"text": "[unk]"})

        indata = np.random.randn(1280, 1).astype(np.float32)
        det._audio_callback(indata, 1280, None, None)

        det.on_wake.assert_not_called()

    def test_cooldown_prevents_retrigger(self):
        """Rapid detections should be suppressed by cooldown."""
        det = self._make_detector_with_recognizer()
        det._last_detection_time = time.monotonic()  # just triggered

        det._recognizer.AcceptWaveform.return_value = True
        det._recognizer.Result.return_value = json.dumps({"text": "nova"})

        indata = np.random.randn(1280, 1).astype(np.float32)
        det._audio_callback(indata, 1280, None, None)

        # Should NOT have triggered due to cooldown
        assert det._paused is False


# =========================================================================
# PorcupineDetector
# =========================================================================

class TestPorcupineDetector:
    """Tests for the Porcupine backend."""

    def test_start_without_key_raises(self):
        """Starting without an access key should raise RuntimeError."""
        cb = MagicMock()
        det = PorcupineDetector(on_wake=cb, wake_word="computer", access_key="")
        with pytest.raises(RuntimeError, match="access key"):
            det.start()

    def test_start_custom_word_without_ppn_raises(self):
        """Custom word without .ppn model should raise RuntimeError."""
        cb = MagicMock()
        det = PorcupineDetector(
            on_wake=cb, wake_word="nova", access_key="test-key", model_path=""
        )
        with pytest.raises(RuntimeError, match="not a Porcupine built-in"):
            det.start()

    @patch("src.wake_word.open_input_stream")
    @patch("pvporcupine.create")
    def test_start_with_builtin_keyword(self, mock_create, mock_stream):
        """Should start successfully with a built-in keyword."""
        mock_porc = MagicMock()
        mock_porc.frame_length = 512
        mock_create.return_value = mock_porc
        mock_s = MagicMock()
        mock_stream.return_value = mock_s

        cb = MagicMock()
        det = PorcupineDetector(
            on_wake=cb, wake_word="computer", access_key="test-key"
        )
        det.start()

        mock_create.assert_called_once_with(
            access_key="test-key",
            keywords=["computer"],
            sensitivities=[0.5],
        )
        mock_s.start.assert_called_once()
        assert det._running is True

        det.stop()
        mock_porc.delete.assert_called_once()

    @patch("src.wake_word.open_input_stream")
    @patch("pvporcupine.create")
    def test_start_with_custom_ppn(self, mock_create, mock_stream):
        """Should start successfully with a custom .ppn model file."""
        mock_porc = MagicMock()
        mock_porc.frame_length = 512
        mock_create.return_value = mock_porc
        mock_stream.return_value = MagicMock()

        cb = MagicMock()
        det = PorcupineDetector(
            on_wake=cb, wake_word="nova",
            access_key="test-key", model_path="/path/to/nova.ppn",
        )
        det.start()

        mock_create.assert_called_once_with(
            access_key="test-key",
            keyword_paths=["/path/to/nova.ppn"],
            sensitivities=[0.5],
        )
        det.stop()

    def test_stop_before_start_is_safe(self):
        cb = MagicMock()
        det = PorcupineDetector(on_wake=cb, wake_word="computer", access_key="")
        det.stop()  # should not raise


# =========================================================================
# KeywordDetector (legacy Whisper-based)
# =========================================================================

class TestKeywordDetectorMatchLogic:
    """Unit-test the keyword matching logic without loading Whisper."""

    def _make_detector(self, keyword: str = "nova") -> KeywordDetector:
        det = KeywordDetector.__new__(KeywordDetector)
        det.keyword = keyword.lower().strip()
        det._match_tokens = KeywordDetector._build_match_tokens(det.keyword)
        det._match_patterns = [
            re.compile(r'\b' + re.escape(tok) + r'\b')
            for tok in det._match_tokens
        ]
        return det

    def test_exact_match(self):
        det = self._make_detector("nova")
        assert det._matches("nova")

    def test_hey_prefix_match(self):
        det = self._make_detector("nova")
        assert det._matches("hey nova")

    def test_embedded_in_sentence(self):
        det = self._make_detector("nova")
        assert det._matches("I said hey nova can you help")

    def test_case_insensitive_match(self):
        det = self._make_detector("nova")
        assert det._matches("NOVA")
        assert det._matches("Hey Nova")

    def test_no_false_positive_on_random_speech(self):
        det = self._make_detector("nova")
        assert not det._matches("the weather is nice today")
        assert not det._matches("tell me about innovations")
        assert not det._matches("")
        assert not det._matches("supernova explosion")
        assert not det._matches("casanova was charming")

    def test_hey_nova_keyword_matches_direct(self):
        det = self._make_detector("hey nova")
        assert det._matches("hey nova")
        assert det._matches("Hey Nova, what's up")

    def test_no_match_on_supernova(self):
        det = self._make_detector("nova")
        assert not det._matches("supernova")
        assert not det._matches("supernova explosion")


class TestKeywordDetectorLifecycle:
    """Test start/stop with mocked audio and model."""

    @patch("src.wake_word.open_input_stream")
    @patch("src.wake_word.KeywordDetector._load_model")
    def test_start_opens_stream(self, mock_load, mock_stream):
        mock_s = MagicMock()
        mock_stream.return_value = mock_s
        cb = MagicMock()
        det = KeywordDetector(keyword="nova", on_wake=cb)
        det.start()
        mock_load.assert_called_once()
        mock_stream.assert_called_once()
        mock_s.start.assert_called_once()
        assert det._running is True
        det.stop()

    @patch("src.wake_word.open_input_stream")
    @patch("src.wake_word.KeywordDetector._load_model")
    def test_stop_cleans_up(self, mock_load, mock_stream):
        mock_s = MagicMock()
        mock_stream.return_value = mock_s
        cb = MagicMock()
        det = KeywordDetector(keyword="nova", on_wake=cb)
        det.start()
        det.stop()
        assert det._running is False
        mock_s.stop.assert_called_once()
        mock_s.close.assert_called_once()

    @patch("src.wake_word.open_input_stream")
    @patch("src.wake_word.KeywordDetector._load_model")
    def test_pause_resume(self, mock_load, mock_stream):
        mock_stream.return_value = MagicMock()
        cb = MagicMock()
        det = KeywordDetector(keyword="nova", on_wake=cb)
        det.start()
        det.pause()
        assert det._paused is True
        det.resume()
        assert det._paused is False
        det.stop()

    @patch("src.wake_word.open_input_stream")
    @patch("src.wake_word.KeywordDetector._load_model")
    def test_start_fails_without_stream(self, mock_load, mock_stream):
        mock_stream.return_value = None
        cb = MagicMock()
        det = KeywordDetector(keyword="nova", on_wake=cb)
        with pytest.raises(RuntimeError, match="Cannot open microphone"):
            det.start()


class TestKeywordDetectorAudioCallback:
    """Test the audio callback with a mocked Whisper model."""

    def _make_detector_with_model(self, keyword="nova"):
        det = KeywordDetector.__new__(KeywordDetector)
        det.keyword = keyword.lower().strip()
        det._match_tokens = KeywordDetector._build_match_tokens(det.keyword)
        det._match_patterns = [
            re.compile(r'\b' + re.escape(tok) + r'\b')
            for tok in det._match_tokens
        ]
        det.on_wake = MagicMock()
        det.buffer_seconds = 1.5
        det.energy_threshold = 0.01
        det.whisper_model_name = "tiny.en"
        det._running = True
        det._paused = False
        det._stream = MagicMock()
        det._lock = threading.Lock()
        det._buf_size = int(16000 * det.buffer_seconds)
        det._audio_buf = np.zeros(det._buf_size, dtype=np.float32)
        det._buf_pos = 0
        det._samples_since_last = 0
        det._last_detection_time = 0.0
        det._model = MagicMock()
        return det

    def test_callback_skips_when_paused(self):
        det = self._make_detector_with_model()
        det._paused = True
        indata = np.random.randn(1280, 1).astype(np.float32)
        det._audio_callback(indata, 1280, None, None)
        det._model.transcribe.assert_not_called()

    def test_callback_skips_when_not_running(self):
        det = self._make_detector_with_model()
        det._running = False
        indata = np.random.randn(1280, 1).astype(np.float32)
        det._audio_callback(indata, 1280, None, None)
        det._model.transcribe.assert_not_called()

    def test_callback_accumulates_before_threshold(self):
        det = self._make_detector_with_model()
        indata = np.random.randn(1280, 1).astype(np.float32) * 0.5
        det._audio_callback(indata, 1280, None, None)
        assert det._samples_since_last == 1280
        det._model.transcribe.assert_not_called()

    def test_energy_gate_skips_silent_buffer(self):
        det = self._make_detector_with_model()
        silent = np.zeros((det._buf_size, 1), dtype=np.float32)
        det._audio_callback(silent, det._buf_size, None, None)
        det._model.transcribe.assert_not_called()

    @patch("src.wake_word.threading.Thread")
    def test_loud_full_buffer_triggers_stt_thread(self, mock_thread_cls):
        det = self._make_detector_with_model()
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread
        loud = np.random.randn(det._buf_size, 1).astype(np.float32) * 0.5
        det._audio_callback(loud, det._buf_size, None, None)
        mock_thread_cls.assert_called_once()
        mock_thread.start.assert_called_once()

    def test_run_stt_fires_callback_on_match(self):
        det = self._make_detector_with_model()
        mock_segment = MagicMock()
        mock_segment.text = "hey nova"
        det._model.transcribe.return_value = ([mock_segment], MagicMock())
        audio = np.random.randn(det._buf_size).astype(np.float32)
        det._run_stt(audio, time.monotonic())
        det.on_wake.assert_called_once()

    def test_run_stt_no_callback_on_no_match(self):
        det = self._make_detector_with_model()
        mock_segment = MagicMock()
        mock_segment.text = "what is the weather"
        det._model.transcribe.return_value = ([mock_segment], MagicMock())
        audio = np.random.randn(det._buf_size).astype(np.float32)
        det._run_stt(audio, time.monotonic())
        det.on_wake.assert_not_called()

    def test_run_stt_cooldown_prevents_retrigger(self):
        det = self._make_detector_with_model()
        mock_segment = MagicMock()
        mock_segment.text = "nova"
        det._model.transcribe.return_value = ([mock_segment], MagicMock())
        audio = np.random.randn(det._buf_size).astype(np.float32)
        det._run_stt(audio, time.monotonic())
        det.on_wake.assert_called_once()
        det._paused = False
        det._run_stt(audio, time.monotonic())
        assert det.on_wake.call_count == 1


# =========================================================================
# OpenWakeWordDetector
# =========================================================================

class TestOpenWakeWordDetector:
    @patch("src.wake_word.open_input_stream")
    def test_start_loads_model_and_opens_stream(self, mock_stream):
        mock_s = MagicMock()
        mock_stream.return_value = mock_s
        cb = MagicMock()

        with patch("openwakeword.model.Model") as MockModel:
            mock_model = MagicMock()
            MockModel.return_value = mock_model

            det = OpenWakeWordDetector(
                on_wake=cb, wake_word="jarvis", confidence_threshold=0.5,
            )
            det.start()

            MockModel.assert_called_once_with(
                wakeword_models=["hey_jarvis_v0.1"],
                inference_framework="onnx",
            )
            mock_s.start.assert_called_once()
            assert det._running is True
            det.stop()

    def test_stop_without_start_is_safe(self):
        cb = MagicMock()
        det = OpenWakeWordDetector(on_wake=cb, wake_word="jarvis")
        det.stop()


# =========================================================================
# WakeWordDetector (facade)
# =========================================================================

class TestWakeWordDetectorFacade:
    """Test that the facade routes to the correct backend."""

    @patch("src.wake_word.VoskKeywordDetector")
    def test_nova_routes_to_vosk(self, MockVosk):
        """'nova' in auto mode should route to VoskKeywordDetector."""
        mock_vosk = MagicMock()
        MockVosk.return_value = mock_vosk
        cb = MagicMock()

        det = WakeWordDetector(on_wake=cb, wake_word="nova", backend="auto")
        assert det.backend_name == "vosk"

        det.start()
        MockVosk.assert_called_once_with(keyword="nova", on_wake=cb)
        mock_vosk.start.assert_called_once()

    @patch("src.wake_word.KeywordDetector")
    def test_nova_forced_keyword_routes_to_whisper(self, MockKW):
        """Forcing 'keyword' backend should use the Whisper-based detector."""
        mock_kw = MagicMock()
        MockKW.return_value = mock_kw
        cb = MagicMock()

        det = WakeWordDetector(on_wake=cb, wake_word="nova", backend="keyword")
        assert det.backend_name == "keyword"

        det.start()
        MockKW.assert_called_once_with(keyword="nova", on_wake=cb)
        mock_kw.start.assert_called_once()

    @patch("src.wake_word.PorcupineDetector")
    def test_porcupine_backend(self, MockPorc):
        """Forcing 'porcupine' backend should use PorcupineDetector."""
        mock_porc = MagicMock()
        MockPorc.return_value = mock_porc
        cb = MagicMock()

        det = WakeWordDetector(on_wake=cb, wake_word="nova", backend="porcupine")
        assert det.backend_name == "porcupine"

        det.start()
        MockPorc.assert_called_once_with(on_wake=cb, wake_word="nova")
        mock_porc.start.assert_called_once()

    @patch("src.wake_word.OpenWakeWordDetector")
    def test_jarvis_routes_to_openwakeword(self, MockOWW):
        mock_oww = MagicMock()
        MockOWW.return_value = mock_oww
        cb = MagicMock()

        det = WakeWordDetector(on_wake=cb, wake_word="jarvis", backend="auto")
        assert det.backend_name == "openwakeword"

        det.start()
        MockOWW.assert_called_once_with(
            on_wake=cb, wake_word="jarvis", confidence_threshold=0.5,
        )
        mock_oww.start.assert_called_once()

    @patch("src.wake_word.OpenWakeWordDetector")
    def test_forced_openwakeword_for_nova(self, MockOWW):
        mock_oww = MagicMock()
        MockOWW.return_value = mock_oww
        cb = MagicMock()

        det = WakeWordDetector(on_wake=cb, wake_word="nova", backend="openwakeword")
        assert det.backend_name == "openwakeword"
        det.start()
        MockOWW.assert_called_once()

    @patch("src.wake_word.VoskKeywordDetector")
    def test_stop_delegates(self, MockVosk):
        mock_vosk = MagicMock()
        MockVosk.return_value = mock_vosk
        cb = MagicMock()
        det = WakeWordDetector(on_wake=cb, wake_word="nova", backend="auto")
        det.start()
        det.stop()
        mock_vosk.stop.assert_called_once()

    @patch("src.wake_word.VoskKeywordDetector")
    def test_pause_resume_delegates(self, MockVosk):
        mock_vosk = MagicMock()
        MockVosk.return_value = mock_vosk
        cb = MagicMock()
        det = WakeWordDetector(on_wake=cb, wake_word="nova", backend="auto")
        det.start()
        det.pause()
        mock_vosk.pause.assert_called_once()
        det.resume()
        mock_vosk.resume.assert_called_once()

    def test_stop_before_start_is_safe(self):
        cb = MagicMock()
        det = WakeWordDetector(on_wake=cb, wake_word="nova", backend="auto")
        det.stop()

    def test_pause_before_start_is_safe(self):
        cb = MagicMock()
        det = WakeWordDetector(on_wake=cb, wake_word="nova", backend="auto")
        det.pause()
        det.resume()


# =========================================================================
# Config integration
# =========================================================================

class TestConfigWakeBackend:
    def test_default_backend_is_auto(self):
        assert config.WAKE_WORD_BACKEND == "auto" or "NOVA_WAKE_BACKEND" in os.environ

    def test_env_override_backend(self, monkeypatch):
        monkeypatch.setenv("NOVA_WAKE_BACKEND", "vosk")
        importlib.reload(config)
        assert config.WAKE_WORD_BACKEND == "vosk"

    def test_keyword_buffer_default(self):
        expected = float(os.environ.get("NOVA_WAKE_KEYWORD_BUFFER", "1.5"))
        assert config.WAKE_KEYWORD_BUFFER_SEC == expected

    def test_keyword_energy_default(self):
        expected = float(os.environ.get("NOVA_WAKE_KEYWORD_ENERGY", "0.01"))
        assert config.WAKE_KEYWORD_ENERGY_THRESHOLD == expected

    def test_keyword_whisper_model_default(self):
        expected = os.environ.get("NOVA_WAKE_KEYWORD_WHISPER", "tiny.en")
        assert config.WAKE_KEYWORD_WHISPER_MODEL == expected

    def test_vosk_model_default(self):
        expected = os.environ.get("NOVA_VOSK_MODEL", "vosk-model-small-en-us-0.15")
        assert config.VOSK_MODEL == expected

    def test_porcupine_key_default_empty(self):
        if "NOVA_PORCUPINE_ACCESS_KEY" not in os.environ:
            assert config.PORCUPINE_ACCESS_KEY == ""

    def test_porcupine_sensitivity_default(self):
        expected = float(os.environ.get("NOVA_PORCUPINE_SENSITIVITY", "0.5"))
        assert config.PORCUPINE_SENSITIVITY == expected
