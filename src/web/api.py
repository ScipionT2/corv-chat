"""
Nova Web Control Hub — FastAPI backend.

Provides REST endpoints and a WebSocket for real-time control of the
Nova pipeline, plus a static single-page dashboard.
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
from src.onboarding import update_env_file

logger = logging.getLogger(__name__)

# ── FastAPI app ───────────────────────────────────────────────────────

app = FastAPI(title="Nova Web Hub", version="1.0.0")

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

# Agent / Skill registries (set via set_registries)
_agent_registry = None
_skill_registry = None


def set_pipeline(pipeline) -> None:
    """Attach a live pipeline instance for control endpoints."""
    global _pipeline, _pipeline_running
    _pipeline = pipeline
    _pipeline_running = pipeline is not None


def set_registries(agent_registry=None, skill_registry=None) -> None:
    """Attach agent and skill registry instances."""
    global _agent_registry, _skill_registry
    if agent_registry is not None:
        _agent_registry = agent_registry
    if skill_registry is not None:
        _skill_registry = skill_registry


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


class CreateAgent(BaseModel):
    """Body for POST /api/agents."""

    name: str
    model: str = "qwen2.5:3b"
    system_prompt: str = ""
    parent_id: Optional[str] = None


class SwitchAgent(BaseModel):
    """Body for POST /api/agents/switch."""

    agent_id: str


# ── Routes ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the single-page dashboard."""
    index = _STATIC_DIR / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Nova Web Hub</h1><p>Dashboard not found.</p>")


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
    """Start the Nova pipeline."""
    global _pipeline_running

    if _pipeline is None:
        return JSONResponse(
            status_code=400,
            content={"error": "No pipeline attached — launch Nova first"},
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

        threading.Thread(target=_do_start, daemon=True, name="nova-web-start").start()
        _pipeline_running = True
        _add_log("info", "Pipeline started via web hub")
        _broadcast({"type": "status", "data": {"pipeline": "running"}})
        return {"status": "starting"}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/api/stop")
async def stop_pipeline():
    """Stop the Nova pipeline."""
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
            from src.memory_legacy import ConversationMemory

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
            content={"error": "No pipeline attached — launch Nova first"},
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


# ── LLM Providers ─────────────────────────────────────────────────────


@app.get("/api/providers")
async def get_providers():
    """Return status of all LLM providers."""
    if _pipeline and hasattr(_pipeline, "llm"):
        llm = _pipeline.llm
        if hasattr(llm, "get_providers_status"):
            return JSONResponse({
                "providers": llm.get_providers_status(),
                "active": llm.get_active_provider(),
            })
    return JSONResponse({"providers": [], "active": "none"})


class SetProviderModel(BaseModel):
    """Body for POST /api/providers/{provider_name}/model."""
    model: str


@app.post("/api/providers/{provider_name}/model")
async def set_provider_model(provider_name: str, body: SetProviderModel):
    """Change model for a provider at runtime."""
    if _pipeline is None or not hasattr(_pipeline, "llm"):
        return JSONResponse(
            status_code=400,
            content={"error": "No pipeline attached"},
        )
    llm = _pipeline.llm
    if not hasattr(llm, "set_model"):
        return JSONResponse(
            status_code=400,
            content={"error": "LLM does not support set_model"},
        )
    llm.set_model(provider_name, body.model)
    _add_log("info", f"Model for {provider_name} changed to {body.model}")
    _broadcast({"type": "provider_model", "data": {"provider": provider_name, "model": body.model}})
    return {"status": "ok", "provider": provider_name, "model": body.model}


# ── Settings / API Keys ───────────────────────────────────────────────


def mask_key(key: str) -> str:
    """Mask an API key for safe display. Never expose full keys."""
    if not key or len(key) < 8:
        return ""
    return key[:5] + "..." + key[-4:]


@app.get("/api/settings")
async def get_settings():
    """Return current settings with masked secrets."""
    return {
        "openrouter_key_set": bool(config.OPENROUTER_API_KEY),
        "openrouter_key_masked": mask_key(config.OPENROUTER_API_KEY),
        "openrouter_model": config.OPENROUTER_MODEL,
        "provider_priority": config.LLM_PROVIDER_PRIORITY,
        "ollama_model": config.OLLAMA_MODEL,
    }


@app.post("/api/settings/openrouter-key")
async def set_openrouter_key(body: dict):
    """Set or update the OpenRouter API key. Writes to .env and reloads in memory."""
    key = body.get("key", "").strip()

    # Validate format if non-empty
    if key and not key.startswith("sk-or-"):
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid key format — should start with 'sk-or-'"},
        )

    # Write to .env file (thread-safe)
    update_env_file("NOVA_OPENROUTER_API_KEY", key)

    # Update in-memory config
    config.OPENROUTER_API_KEY = key

    # Reinitialize providers if pipeline is available
    if _pipeline and hasattr(_pipeline, "llm"):
        llm = _pipeline.llm
        if hasattr(llm, "_init_providers"):
            try:
                llm._init_providers()
                _add_log("info", "LLM providers reinitialized after API key update")
            except Exception as exc:
                _add_log("warning", f"Provider reinit failed: {exc}")

    action = "set" if key else "removed"
    _add_log("info", f"OpenRouter API key {action} via web hub")
    _broadcast({"type": "settings", "data": {"openrouter_key_set": bool(key)}})

    return {"status": "ok", "key_set": bool(key)}


# ── Agents & Skills ──────────────────────────────────────────────────

@app.get("/api/agents")
async def list_agents():
    """List all registered agents."""
    if _agent_registry is None:
        return JSONResponse(status_code=400, content={"error": "Agent registry not initialized"})

    agents = _agent_registry.list_agents()
    active = _agent_registry.get_active()
    return {
        "agents": [a.model_dump(mode="json") for a in agents],
        "active_id": active.id if active else None,
    }


@app.post("/api/agents")
async def create_agent(body: CreateAgent):
    """Create a new agent."""
    if _agent_registry is None:
        return JSONResponse(status_code=400, content={"error": "Agent registry not initialized"})

    from datetime import datetime
    from src.agents.models import AgentConfig

    slug = body.name.lower().replace(" ", "-")
    agent = AgentConfig(
        id=slug,
        name=body.name,
        system_prompt=body.system_prompt or config.LLM_SYSTEM_PROMPT,
        model=body.model,
        parent_id=body.parent_id,
        created_at=datetime.now(),
    )
    _agent_registry.register(agent)
    _add_log("info", f"Agent created: {agent.name} ({agent.id})")
    return {"status": "ok", "agent": agent.model_dump(mode="json")}


@app.post("/api/agents/switch")
async def switch_agent(body: SwitchAgent):
    """Switch the active agent."""
    if _agent_registry is None:
        return JSONResponse(status_code=400, content={"error": "Agent registry not initialized"})

    try:
        _agent_registry.set_active(body.agent_id)
        active = _agent_registry.get_active()

        # Update pipeline LLM if available
        if _pipeline and hasattr(_pipeline, "llm"):
            _pipeline.llm.system_prompt = active.system_prompt

        _add_log("info", f"Switched to agent: {active.name}")
        _broadcast({"type": "agent_switch", "data": {"agent_id": active.id, "name": active.name}})
        return {"status": "ok", "active": active.model_dump(mode="json")}
    except ValueError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})


@app.get("/api/skills")
async def list_skills():
    """List all loaded skills."""
    if _skill_registry is None:
        return JSONResponse(status_code=400, content={"error": "Skill registry not initialized"})

    skills = _skill_registry.list_skills()
    return {"skills": [s.model_dump(mode="json") for s in skills]}


@app.post("/api/skills/reload")
async def reload_skills():
    """Re-scan and reload all skills."""
    if _skill_registry is None:
        return JSONResponse(status_code=400, content={"error": "Skill registry not initialized"})

    _skill_registry.reload()
    skills = _skill_registry.list_skills()
    _add_log("info", f"Skills reloaded: {len(skills)} found")
    return {"status": "ok", "count": len(skills)}


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


# ═══════════════════════════════════════════════════════════════════════
#  V2 API — Multi-Provider Chat System
# ═══════════════════════════════════════════════════════════════════════

from src.web.providers import get_available_models, chat_stream, MODELS


class ChatV2Message(BaseModel):
    """Body for POST /api/v2/chat."""
    message: str
    model: str = "gemini-2.0-flash"  # default to free model
    history: list[dict] = []
    api_key: Optional[str] = None  # BYOK key (never stored)


@app.get("/api/v2/models")
async def get_models():
    """Return available models with their status."""
    return JSONResponse({"models": get_available_models()})


@app.post("/api/v2/chat")
async def chat_v2(body: ChatV2Message):
    """Multi-provider chat with SSE streaming.

    Free models use server-side keys.
    BYOK models require api_key in the request body.
    API keys are NEVER stored — used for this request only.
    """
    from sse_starlette.sse import EventSourceResponse

    _add_log("info", f"Chat v2 [{body.model}]: {body.message[:80]}")

    # Build messages list
    messages = []
    for msg in body.history[-20:]:  # cap at last 20 messages
        if msg.get("role") and msg.get("content"):
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": body.message})

    async def _stream():
        accumulated = ""
        try:
            async for token in chat_stream(
                model_key=body.model,
                messages=messages,
                user_api_key=body.api_key,
            ):
                accumulated += token
                yield {"event": "token", "data": json.dumps({"token": token})}
        except ValueError as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)})}
            return
        except Exception as e:
            logger.error(f"Chat v2 error: {e}")
            yield {"event": "error", "data": json.dumps({"error": "Something went wrong. Please try again."})}
            return

        yield {"event": "done", "data": json.dumps({"content": accumulated})}

    return EventSourceResponse(_stream())
