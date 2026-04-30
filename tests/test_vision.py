"""Tests for the vision module — screen capture and Ollama vision client."""

import base64
import json
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.vision import (
    VisionClient,
    VisionResult,
    AnalysisMode,
    capture_screen,
    image_to_base64,
    VISION_PROMPT,
)


# ─── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_png():
    """Create a minimal valid PNG for testing."""
    try:
        from PIL import Image
        img = Image.new("RGB", (100, 100), color="red")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        # Minimal PNG header for tests without Pillow
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


@pytest.fixture
def vision_client():
    return VisionClient(base_url="http://localhost:11434", model="test-vision")


@pytest.fixture
def mock_analysis_callback():
    return MagicMock()


# ─── image_to_base64 ─────────────────────────────────────────────────

class TestBase64Encoding:
    def test_encode_bytes(self, mock_png):
        result = image_to_base64(mock_png)
        assert isinstance(result, str)
        # Should be valid base64
        decoded = base64.b64decode(result)
        assert decoded == mock_png

    def test_roundtrip(self, mock_png):
        encoded = image_to_base64(mock_png)
        decoded = base64.b64decode(encoded)
        assert decoded == mock_png


# ─── VisionClient ────────────────────────────────────────────────────

class TestVisionClient:
    @patch("src.vision.requests.post")
    def test_analyze_image_success(self, mock_post, vision_client, mock_png):
        # Simulate streaming Ollama response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines.return_value = [
            json.dumps({"response": "I see a ", "done": False}).encode(),
            json.dumps({"response": "red screen.", "done": True}).encode(),
        ]
        mock_post.return_value = mock_resp

        result = vision_client.analyze_image(mock_png, prompt="What do you see?")

        assert result is not None
        assert isinstance(result, VisionResult)
        assert "red screen" in result.analysis
        assert result.model == "test-vision"
        assert result.elapsed_ms > 0
        assert result.frame_size_bytes == len(mock_png)

    @patch("src.vision.requests.post")
    def test_analyze_image_empty_response(self, mock_post, vision_client, mock_png):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines.return_value = [
            json.dumps({"response": "", "done": True}).encode(),
        ]
        mock_post.return_value = mock_resp

        result = vision_client.analyze_image(mock_png)
        assert result is None

    @patch("src.vision.requests.post")
    def test_analyze_image_connection_error(self, mock_post, vision_client, mock_png):
        import requests
        mock_post.side_effect = requests.ConnectionError("refused")

        result = vision_client.analyze_image(mock_png)
        assert result is None

    @patch("src.vision.requests.post")
    def test_analyze_image_timeout(self, mock_post, vision_client, mock_png):
        import requests
        mock_post.side_effect = requests.Timeout("timed out")

        result = vision_client.analyze_image(mock_png)
        assert result is None

    @patch("src.vision.requests.post")
    def test_analyze_sends_correct_payload(self, mock_post, vision_client, mock_png):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines.return_value = [
            json.dumps({"response": "test", "done": True}).encode(),
        ]
        mock_post.return_value = mock_resp

        vision_client.analyze_image(mock_png, prompt="custom prompt")

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["model"] == "test-vision"
        assert payload["prompt"] == "custom prompt"
        assert len(payload["images"]) == 1
        assert payload["stream"] is True

    @patch("src.vision.requests.get")
    def test_check_model_available_true(self, mock_get, vision_client):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "models": [{"name": "test-vision:latest"}, {"name": "llama3:8b"}]
        }
        mock_get.return_value = mock_resp

        assert vision_client.check_model_available() is True

    @patch("src.vision.requests.get")
    def test_check_model_available_false(self, mock_get, vision_client):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "models": [{"name": "llama3:8b"}]
        }
        mock_get.return_value = mock_resp

        assert vision_client.check_model_available() is False

    @patch("src.vision.requests.get")
    def test_check_model_connection_error(self, mock_get, vision_client):
        mock_get.side_effect = Exception("connection failed")
        assert vision_client.check_model_available() is False


# ─── AnalysisMode ─────────────────────────────────────────────────────

class TestAnalysisMode:
    def test_initial_state(self, mock_analysis_callback):
        mode = AnalysisMode(on_result=mock_analysis_callback)
        assert mode.active is False
        assert mode.results == []
        assert mode.latest is None

    def test_toggle_on(self, mock_analysis_callback):
        mode = AnalysisMode(on_result=mock_analysis_callback, interval=100)
        result = mode.toggle()
        assert result is True
        assert mode.active is True
        mode.stop()

    def test_toggle_off(self, mock_analysis_callback):
        mode = AnalysisMode(on_result=mock_analysis_callback, interval=100)
        mode.toggle()  # on
        result = mode.toggle()  # off
        assert result is False
        assert mode.active is False

    def test_start_stop(self, mock_analysis_callback):
        mode = AnalysisMode(on_result=mock_analysis_callback, interval=100)
        mode.start()
        assert mode.active is True
        mode.stop()
        assert mode.active is False

    def test_double_start_is_safe(self, mock_analysis_callback):
        mode = AnalysisMode(on_result=mock_analysis_callback, interval=100)
        mode.start()
        mode.start()  # Should not raise
        assert mode.active is True
        mode.stop()

    def test_double_stop_is_safe(self, mock_analysis_callback):
        mode = AnalysisMode(on_result=mock_analysis_callback, interval=100)
        mode.stop()
        mode.stop()  # Should not raise

    @patch("src.vision.capture_screen")
    def test_analyze_once(self, mock_capture, mock_analysis_callback, mock_png):
        mock_capture.return_value = mock_png

        mock_client = MagicMock()
        mock_client.analyze_image.return_value = VisionResult(
            timestamp=datetime.now(),
            analysis="Test analysis",
            model="test",
            elapsed_ms=100,
            frame_size_bytes=len(mock_png),
        )

        mode = AnalysisMode(
            on_result=mock_analysis_callback,
            vision_client=mock_client,
        )
        result = mode.analyze_once()

        assert result is not None
        assert result.analysis == "Test analysis"
        assert len(mode.results) == 1
        assert mode.latest == result

    @patch("src.vision.capture_screen")
    def test_analyze_once_capture_failure(self, mock_capture, mock_analysis_callback):
        mock_capture.return_value = None

        mode = AnalysisMode(on_result=mock_analysis_callback)
        result = mode.analyze_once()
        assert result is None

    @patch("src.vision.capture_screen")
    def test_analyze_once_with_custom_prompt(self, mock_capture, mock_analysis_callback, mock_png):
        mock_capture.return_value = mock_png

        mock_client = MagicMock()
        mock_client.analyze_image.return_value = VisionResult(
            timestamp=datetime.now(),
            analysis="Custom result",
            model="test",
            elapsed_ms=50,
        )

        mode = AnalysisMode(
            on_result=mock_analysis_callback,
            vision_client=mock_client,
        )
        result = mode.analyze_once(prompt="Custom prompt here")

        mock_client.analyze_image.assert_called_once()
        call_args = mock_client.analyze_image.call_args
        assert call_args.kwargs.get("prompt") == "Custom prompt here"


# ─── VisionResult ─────────────────────────────────────────────────────

class TestVisionResult:
    def test_create_result(self):
        r = VisionResult(
            timestamp=datetime.now(),
            analysis="Test",
            model="llama3.2-vision",
            elapsed_ms=500,
            frame_size_bytes=1024,
        )
        assert r.analysis == "Test"
        assert r.elapsed_ms == 500
        assert r.frame_size_bytes == 1024

    def test_result_defaults(self):
        r = VisionResult(
            timestamp=datetime.now(),
            analysis="Test",
            model="test",
            elapsed_ms=0,
        )
        assert r.frame_size_bytes == 0


# ─── Screen Capture (mocked) ─────────────────────────────────────────

class TestScreenCapture:
    @patch("src.vision.mss", create=True)
    @patch("src.vision.Image", create=True)
    def test_capture_returns_bytes_or_none(self, mock_image_mod, mock_mss_mod):
        """capture_screen should return bytes or None, never raise."""
        # We can't easily mock the full mss pipeline, so just verify
        # the function handles import errors gracefully
        result = capture_screen()
        # Either bytes or None is acceptable
        assert result is None or isinstance(result, bytes)
