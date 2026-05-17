"""
Status and health HTTP server for Nova.

Exposes ``/health`` and ``/status`` JSON endpoints on a configurable
port (default 8765).  Designed to run in a background thread alongside
the voice pipeline.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Optional

import config

logger = logging.getLogger(__name__)


class NovaStats:
    """Thread-safe statistics collector for Nova.

    Attributes
    ----------
    start_time:
        Unix timestamp when Nova started.
    total_queries:
        Total number of user queries processed.
    last_query:
        Text of the most recent user query, or ``None``.
    last_query_time:
        Unix timestamp of the last query, or ``None``.
    wake_word_count:
        Number of wake-word activations detected.
    model:
        The current Ollama model name.
    """

    def __init__(self, model: str = config.OLLAMA_MODEL) -> None:
        self.start_time: float = time.time()
        self.total_queries: int = 0
        self.last_query: Optional[str] = None
        self.last_query_time: Optional[float] = None
        self.wake_word_count: int = 0
        self.model: str = model
        self._lock = threading.Lock()

    def record_query(self, text: str) -> None:
        """Record a new user query.

        Parameters
        ----------
        text:
            The user's transcribed query text.
        """
        with self._lock:
            self.total_queries += 1
            self.last_query = text
            self.last_query_time = time.time()

    def record_wake(self) -> None:
        """Record a wake-word activation."""
        with self._lock:
            self.wake_word_count += 1

    @property
    def uptime_seconds(self) -> float:
        """Return seconds since start."""
        return time.time() - self.start_time

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable status dict."""
        with self._lock:
            return {
                "status": "running",
                "uptime_seconds": round(self.uptime_seconds, 1),
                "total_queries": self.total_queries,
                "last_query": self.last_query,
                "last_query_time": self.last_query_time,
                "wake_word_count": self.wake_word_count,
                "model": self.model,
            }


class HealthHandler(BaseHTTPRequestHandler):
    """HTTP request handler for /health and /status endpoints."""

    # Class-level reference to stats (set before server starts)
    stats: Optional[NovaStats] = None

    def do_GET(self) -> None:  # noqa: N802
        """Handle GET requests."""
        if self.path == "/health":
            self._json_response(200, {"status": "ok"})
        elif self.path == "/status":
            if self.stats is not None:
                self._json_response(200, self.stats.to_dict())
            else:
                self._json_response(503, {"status": "not_initialized"})
        else:
            self._json_response(404, {"error": "not_found"})

    def _json_response(self, code: int, data: dict[str, Any]) -> None:
        """Write a JSON HTTP response."""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Suppress default stderr logging; use our logger instead."""
        logger.debug("Health HTTP: %s", format % args)


class HealthServer:
    """Background HTTP server for health/status monitoring.

    Parameters
    ----------
    stats:
        The :class:`NovaStats` instance to expose.
    port:
        TCP port to listen on.
    """

    def __init__(
        self,
        stats: NovaStats,
        port: int = config.HEALTH_PORT,
    ) -> None:
        self.stats = stats
        self.port = port
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the HTTP server in a background daemon thread."""
        # Create a handler class with stats bound
        handler_class = type(
            "BoundHealthHandler",
            (HealthHandler,),
            {"stats": self.stats},
        )

        try:
            self._server = HTTPServer(("0.0.0.0", self.port), handler_class)
        except OSError as exc:
            logger.error("Cannot start health server on port %d: %s", self.port, exc)
            return

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="health-server",
        )
        self._thread.start()
        logger.info("Health server started on port %d", self.port)

    def stop(self) -> None:
        """Shut down the HTTP server."""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        logger.info("Health server stopped")

    @property
    def is_running(self) -> bool:
        """Return ``True`` if the server thread is alive."""
        return self._thread is not None and self._thread.is_alive()


# Backward compat aliases
EPAgentStats = NovaStats  # Legacy alias
JarvisStats = NovaStats  # Legacy alias
