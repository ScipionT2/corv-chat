"""Tests for the Ollama LLM client."""

import json
from unittest.mock import patch, MagicMock

import pytest

from src.llm import OllamaClient


@pytest.fixture
def client() -> OllamaClient:
    """Return a fresh OllamaClient with test defaults."""
    c = OllamaClient(
        base_url="http://localhost:11434",
        model="test-model",
        system_prompt="You are a test assistant.",
        max_history=3,
        timeout=10,
    )
    # Replace the internal session with a mock so tests can patch .post
    c._session = MagicMock()
    return c


def _make_stream_response(text: str) -> MagicMock:
    """Create a mock requests.Response that yields streaming Ollama JSON."""
    tokens = text.split()
    lines = []
    for i, token in enumerate(tokens):
        chunk = {
            "message": {"role": "assistant", "content": token + " "},
            "done": i == len(tokens) - 1,
        }
        lines.append(json.dumps(chunk).encode())
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_lines.return_value = iter(lines)
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


class TestOllamaClientChat:
    """Tests for the chat method."""

    def test_basic_chat(self, client):
        client._session.post.return_value = _make_stream_response("Hello there user")
        reply = client.chat("Hi")
        assert reply is not None
        assert "Hello" in reply

    def test_history_grows(self, client):
        client._session.post.return_value = _make_stream_response("reply one")
        client.chat("msg one")
        assert len(client.history) == 2  # user + assistant

        client._session.post.return_value = _make_stream_response("reply two")
        client.chat("msg two")
        assert len(client.history) == 4

    def test_history_trimmed(self, client):
        """History should be trimmed to max_history pairs (3 pairs = 6 messages)."""
        for i in range(5):
            client._session.post.return_value = _make_stream_response(f"reply {i}")
            client.chat(f"message {i}")
        # max_history=3 → 6 messages max
        assert len(client.history) <= 6

    def test_clear_history(self, client):
        client._session.post.return_value = _make_stream_response("ok")
        client.chat("hello")
        assert len(client.history) > 0
        client.clear_history()
        assert len(client.history) == 0

    def test_system_prompt_in_messages(self, client):
        client._session.post.return_value = _make_stream_response("ok")
        client.chat("test")
        call_args = client._session.post.call_args
        payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
        messages = payload["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a test assistant."

    def test_streaming_enabled(self, client):
        client._session.post.return_value = _make_stream_response("ok")
        client.chat("test")
        call_args = client._session.post.call_args
        payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
        assert payload["stream"] is True

    def test_chat_failure_returns_none(self, client):
        client._session.post.side_effect = Exception("timeout")
        result = client.chat("hello")
        assert result is None

    def test_chat_failure_removes_pending_user_msg(self, client):
        client._session.post.side_effect = Exception("timeout")
        client.chat("hello")
        # The failed user message should be removed
        assert len(client.history) == 0

    def test_connection_error_returns_none(self, client):
        import requests
        client._session.post.side_effect = requests.ConnectionError("refused")
        result = client.chat("hello")
        assert result is None

    def test_empty_stream_returns_none(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = iter([])
        mock_resp.raise_for_status = MagicMock()
        client._session.post.return_value = mock_resp
        result = client.chat("hello")
        assert result is None
