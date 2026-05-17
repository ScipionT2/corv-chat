"""Tests for the status and health HTTP server."""

import json
import time
import urllib.request
from unittest.mock import patch

import pytest

from src.health import NovaStats, HealthServer


@pytest.fixture
def stats() -> NovaStats:
    """Return a fresh NovaStats instance."""
    return NovaStats(model="test-model")


@pytest.fixture
def server(stats: NovaStats) -> HealthServer:
    """Start a health server on a random port and tear it down after the test."""
    # Use port 0 to let the OS pick a free port
    srv = HealthServer(stats=stats, port=0)
    srv.start()
    # Get the actual port
    assert srv._server is not None
    yield srv
    srv.stop()


def _get_port(server: HealthServer) -> int:
    """Get the actual port the server bound to."""
    return server._server.server_address[1]


class TestNovaStats:
    """Tests for the stats collector."""

    def test_initial_state(self, stats: NovaStats) -> None:
        assert stats.total_queries == 0
        assert stats.wake_word_count == 0
        assert stats.last_query is None
        assert stats.model == "test-model"

    def test_record_query(self, stats: NovaStats) -> None:
        stats.record_query("What is AI?")
        assert stats.total_queries == 1
        assert stats.last_query == "What is AI?"
        assert stats.last_query_time is not None

    def test_record_multiple_queries(self, stats: NovaStats) -> None:
        stats.record_query("first")
        stats.record_query("second")
        stats.record_query("third")
        assert stats.total_queries == 3
        assert stats.last_query == "third"

    def test_record_wake(self, stats: NovaStats) -> None:
        stats.record_wake()
        stats.record_wake()
        assert stats.wake_word_count == 2

    def test_uptime_increases(self, stats: NovaStats) -> None:
        t1 = stats.uptime_seconds
        time.sleep(0.05)
        t2 = stats.uptime_seconds
        assert t2 > t1

    def test_to_dict(self, stats: NovaStats) -> None:
        stats.record_query("hello")
        stats.record_wake()
        d = stats.to_dict()
        assert d["status"] == "running"
        assert d["total_queries"] == 1
        assert d["wake_word_count"] == 1
        assert d["model"] == "test-model"
        assert d["last_query"] == "hello"
        assert "uptime_seconds" in d

    def test_to_dict_initial(self, stats: NovaStats) -> None:
        d = stats.to_dict()
        assert d["total_queries"] == 0
        assert d["last_query"] is None


class TestHealthServer:
    """Tests for the HTTP endpoints."""

    def test_health_endpoint(self, server: HealthServer) -> None:
        port = _get_port(server)
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/health")
        data = json.loads(resp.read())
        assert data["status"] == "ok"
        assert resp.status == 200

    def test_status_endpoint(self, server: HealthServer, stats: NovaStats) -> None:
        stats.record_query("test query")
        stats.record_wake()
        port = _get_port(server)
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/status")
        data = json.loads(resp.read())
        assert data["status"] == "running"
        assert data["total_queries"] == 1
        assert data["wake_word_count"] == 1
        assert data["last_query"] == "test query"
        assert data["model"] == "test-model"

    def test_404_for_unknown_path(self, server: HealthServer) -> None:
        port = _get_port(server)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/unknown")
            pytest.fail("Expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404

    def test_server_is_running(self, server: HealthServer) -> None:
        assert server.is_running

    def test_stop_server(self, stats: NovaStats) -> None:
        srv = HealthServer(stats=stats, port=0)
        srv.start()
        assert srv.is_running
        srv.stop()
        time.sleep(0.1)
        assert not srv.is_running

    def test_health_content_type(self, server: HealthServer) -> None:
        port = _get_port(server)
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/health")
        assert "application/json" in resp.headers.get("Content-Type", "")
