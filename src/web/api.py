"""
EP Agent Web Control Hub — FastAPI backend.

Provides REST endpoints and a WebSocket for real-time control of the
EP Agent pipeline, plus a static single-page dashboard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config

logger = logging.getLogger(__name__)

# ── FastAPI app ───────────────────────────────────────────────────────

app = FastAPI(title="EP Agent Web Hub", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (dashboard HTML/CSS/JS)
_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# ── Shared state ──────────────────────────────────────────────────────

_pipeline = None
_pipeline_running = False
_start_time = time.time()
_logs: list[dict[str, Any]] = []
_MAX_LOGS = 200

# Connected WebSocket clients
_ws_clients: set[WebSocket] = set()


def set_pipeline(pipeline) -> None:
    """Attach a live pipeline instance for control endpoints."""
    global _pipeline, _pipeline_running
    _pipeline = pipeline
    _pipeline_running = pipeline is not None


def _add_log(level: str, message: str) -> None:
    """Append a log entry and broadcast via WebSocket."""
    entry = {
        "timestamp": time.time(),
        "level": level,
        "message": message,
    }
    _logs.append(entry)
    if len(_logs) > _MAX_LOGS:
        _logs[:] = _logs[-_MAX_LOGS:]
    _broadcast({"type": "log", "data": entry})


def _broadcast(message: dict) -> None:
    """Send a JSON message to all connected WebSocket clients."""
    dead: list[WebSocket] = []
    for ws in _ws_clients:
        try:
            asyncio.get_event_loop().create_task(ws.send_json(message))
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


# ── Pydantic models ──────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    """Body for POST /api/config."""

    voice: Optional[str] = None
    model: Optional[str] = None
    accent: Optional[str] = None
    theme: Optional[str] = None


class ChatMessage(BaseModel):
    """Body for POST /api/chat."""

    message: str


# ── Routes ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the single-page dashboard."""
    index = _STATIC_DIR / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>EP Agent Web Hub</h1><p>Dashboard not found.</p>")


@app.get("/api/status")
async def get_status():
    """Return system status: Ollama, models, pipeline, vision."""
    from src.ollama_manager import get_manager

    mgr = get_manager()
    health = mgr.get_health_status()

    pipeline_state = "stopped"
    if _pipeline is not None:
        if getattr(_pipeline, "_running", False):
            pipeline_state = "running"
        else:
            pipeline_state = "idle"

    vision_state = "disabled"
    if _pipeline and getattr(_pipeline, "_vision_enabled", False):
        am = getattr(_pipeline, "analysis_mode", None)
        if am:
            if getattr(am, "sleeping", False):
                vision_state = "sleeping"
            elif getattr(am, "active", False):
                vision_state = "active"
            else:
                vision_state = "ready"

    # LLM mode
    llm_mode = "local"
    if _pipeline and hasattr(_pipeline, "llm"):
        llm_mode = getattr(_pipeline.llm, "mode", "local")
        if callable(llm_mode):
            llm_mode = llm_mode  # property already resolved

    return {
        "pipeline": pipeline_state,
        "vision": vision_state,
        "llm_mode": llm_mode,
        "uptime_seconds": round(time.time() - _start_time, 1),
        **health,
    }


@app.post("/api/start")
async def start_pipeline():
    """Start the EP Agent pipeline."""
    global _pipeline_running

    if _pipeline is None:
        return JSONResponse(
            status_code=400,
            content={"error": "No pipeline attached — launch EP Agent first"},
        )

    if getattr(_pipeline, "_running", False):
        return {"status": "already_running"}

    try:
        import threading

        def _do_start():
            try:
                _pipeline.start()
            except Exception as exc:
                _add_log("error", f"Pipeline start failed: {exc}")

        threading.Thread(target=_do_start, daemon=True, name="web-pipeline-start").start()
        _pipeline_running = True
        _add_log("info", "Pipeline started via web hub")
        _broadcast({"type": "status", "data": {"pipeline": "running"}})
        return {"status": "starting"}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/api/stop")
async def stop_pipeline():
    """Stop the EP Agent pipeline."""
    global _pipeline_running

    if _pipeline is None:
        return JSONResponse(
            status_code=400,
            content={"error": "No pipeline attached"},
        )

    try:
        _pipeline.stop()
        _pipeline_running = False
        _add_log("info", "Pipeline stopped via web hub")
        _broadcast({"type": "status", "data": {"pipeline": "stopped"}})
        return {"status": "stopped"}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/api/config")
async def get_config():
    """Return current configuration values."""
    voice = config.MACOS_SAY_VOICE
    if _pipeline and hasattr(_pipeline, "tts"):
        voice = getattr(_pipeline.tts, "say_voice", voice)

    return {
        "voice": voice,
        "model": config.OLLAMA_MODEL,
        "vision_model": config.VISION_MODEL,
        "accent": config.ACCENT_COLOR,
        "wake_word": config.WAKE_WORD,
        "vision_enabled": config.VISION_ENABLED,
        "hybrid_mode": config.HYBRID_MODE,
        "tts_backend": config.TTS_BACKEND,
        "whisper_model": config.WHISPER_MODEL,
        "log_level": config.LOG_LEVEL,
    }


@app.post("/api/config")
async def update_config(body: ConfigUpdate):
    """Update configuration values at runtime."""
    changes: dict[str, str] = {}

    if body.voice is not None and _pipeline and hasattr(_pipeline, "tts"):
        _pipeline.tts.say_voice = body.voice
        changes["voice"] = body.voice

    if body.model is not None:
        config.OLLAMA_MODEL = body.model
        changes["model"] = body.model

    if body.accent is not None:
        config.ACCENT_COLOR = body.accent
        changes["accent"] = body.accent

    if body.theme is not None:
        changes["theme"] = body.theme

    if changes:
        _add_log("info", f"Config updated: {changes}")
        _broadcast({"type": "config", "data": changes})

    return {"status": "ok", "changes": changes}


@app.get("/api/history")
async def get_history():
    """Return recent conversation history."""
    history: list[dict[str, str]] = []

    # Try pipeline LLM history first
    if _pipeline and hasattr(_pipeline, "llm"):
        llm = _pipeline.llm
        if hasattr(llm, "history"):
            raw = llm.history
            if callable(raw):
                raw = raw()
            history = list(raw) if raw else []

    # Also try persistent memory
    if not history:
        try:
            from src.memory import ConversationMemory

            mem = ConversationMemory()
            history = mem.load()
        except Exception:
            pass

    return {"history": history[-50:]}  # Last 50 entries


@app.post("/api/chat")
async def chat(body: ChatMessage):
    """Send a chat message to the pipeline and return the response.

    Uses server-sent events (SSE) style: streams tokens as they arrive.
    Falls back to a full response if streaming is not available.
    """
    from sse_starlette.sse import EventSourceResponse

    if _pipeline is None:
        return JSONResponse(
            status_code=400,
            content={"error": "No pipeline attached — launch EP Agent first"},
        )

    _add_log("info", f"Chat: {body.message[:80]}")
    _broadcast({"type": "chat", "data": {"role": "user", "content": body.message}})

    async def _event_generator():
        """Yield SSE events with streamed tokens."""
        llm = getattr(_pipeline, "llm", None)
        if llm is None:
            yield {"event": "error", "data": "LLM not available"}
            return

        accumulated = ""
        try:
            if hasattr(llm, "chat_stream"):
                for token in llm.chat_stream(body.message):
                    accumulated += token
                    yield {"event": "token", "data": token}
                    await asyncio.sleep(0)  # yield control
            else:
                reply = llm.chat(body.message)
                if reply:
                    accumulated = reply
                    yield {"event": "token", "data": reply}
                else:
                    yield {"event": "error", "data": "No response from LLM"}
                    return
        except Exception as exc:
            yield {"event": "error", "data": str(exc)}
            return

        yield {"event": "done", "data": accumulated}
        _broadcast({
            "type": "chat",
            "data": {"role": "assistant", "content": accumulated},
        })

    return EventSourceResponse(_event_generator())


@app.get("/api/vision/analyze")
async def vision_analyze():
    """Trigger a one-shot screen analysis."""
    if _pipeline is None or not getattr(_pipeline, "_vision_enabled", False):
        return JSONResponse(
            status_code=400,
            content={"error": "Vision not available"},
        )

    am = getattr(_pipeline, "analysis_mode", None)
    if am is None:
        return JSONResponse(
            status_code=400,
            content={"error": "Analysis mode not initialized"},
        )

    _add_log("info", "Vision: one-shot analysis triggered from web hub")

    try:
        result = am.analyze_once()
        if result:
            _broadcast({
                "type": "vision",
                "data": {
                    "analysis": result.analysis,
                    "elapsed_ms": result.elapsed_ms,
                    "timestamp": time.time(),
                },
            })
            return {
                "analysis": result.analysis,
                "elapsed_ms": result.elapsed_ms,
            }
        return JSONResponse(
            status_code=500,
            content={"error": "Analysis returned no result"},
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/api/logs")
async def get_logs():
    """Return recent log entries."""
    return {"logs": _logs[-100:]}


# ── WebSocket ─────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Real-time updates: status changes, chat messages, vision results, logs."""
    await ws.accept()
    _ws_clients.add(ws)
    logger.info("WebSocket client connected (%d total)", len(_ws_clients))

    try:
        # Send current status on connect
        from src.ollama_manager import get_manager

        mgr = get_manager()
        health = mgr.get_health_status()
        await ws.send_json({"type": "status", "data": health})

        # Keep alive — read client messages (ping/commands)
        while True:
            data = await ws.receive_text()
            # Handle client pings
            if data == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _ws_clients.discard(ws)
        logger.info("WebSocket client disconnected (%d remaining)", len(_ws_clients))
