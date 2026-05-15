"""
EP Agent Web Control Hub — integration module.

Provides ``start_web_server()`` and ``stop_web_server()`` to run the
FastAPI dashboard alongside the voice pipeline in a background thread.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_server_thread: Optional[threading.Thread] = None
_shutdown_event = threading.Event()


def start_web_server(
    host: str = "0.0.0.0",
    port: int = 8766,
    pipeline=None,
) -> None:
    """Start the FastAPI web hub in a background daemon thread.

    Parameters
    ----------
    host:
        Bind address.
    port:
        TCP port (default 8766).
    pipeline:
        Optional :class:`EPAgentPipeline` instance for live control.
    """
    global _server_thread

    if _server_thread is not None and _server_thread.is_alive():
        logger.warning("Web server already running — skipping start")
        return

    # Lazy import so the rest of the app doesn't need fastapi installed
    from src.web.api import app, set_pipeline  # noqa: F811

    if pipeline is not None:
        set_pipeline(pipeline)

    _shutdown_event.clear()

    def _run() -> None:
        try:
            import uvicorn

            uvi_config = uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level="warning",
                access_log=False,
            )
            server = uvicorn.Server(uvi_config)
            # Store server ref so stop_web_server can shut it down
            _run._server = server  # type: ignore[attr-defined]
            server.run()
        except Exception as exc:
            logger.error("Web server failed: %s", exc)

    _server_thread = threading.Thread(target=_run, name="web-hub", daemon=True)
    _server_thread.start()
    logger.info("Web Control Hub started on http://%s:%d", host, port)


def stop_web_server() -> None:
    """Signal the background uvicorn server to shut down."""
    global _server_thread

    if _server_thread is None:
        return

    # Try to reach the uvicorn.Server instance stored on the thread target
    try:
        run_fn = _server_thread._target  # type: ignore[attr-defined]
        server = getattr(run_fn, "_server", None)
        if server is not None:
            server.should_exit = True
    except Exception:
        pass

    _shutdown_event.set()
    _server_thread = None
    logger.info("Web Control Hub stopped")
