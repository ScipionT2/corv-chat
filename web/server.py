"""
Nova Web — Standalone FastAPI backend.

Multi-provider LLM chat (OpenRouter + OpenAI) with streaming SSE,
per-session conversation history, custom agents, and settings management.

Run:  uvicorn server:app --host 0.0.0.0 --port 8766
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv(Path.cwd() / ".env")

OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
DEFAULT_MODEL: str = os.getenv("NOVA_DEFAULT_MODEL", "")
PORT: int = int(os.getenv("PORT", "8766"))
CUSTOM_SYSTEM_PROMPT: str = os.getenv("NOVA_SYSTEM_PROMPT", "")

BOOT_TIME: float = time.time()

# ---------------------------------------------------------------------------
# Provider helpers
# ---------------------------------------------------------------------------

NOVA_DEFAULT_SYSTEM = (
    "You are Nova, a sharp and helpful AI assistant. "
    "Be direct, concise, and genuinely useful. "
    "You have personality — feel free to be witty when it fits, "
    "but always prioritize giving great answers."
)


def _resolve_provider() -> str:
    """Return 'openrouter', 'openai', or 'none'."""
    if OPENROUTER_API_KEY:
        return "openrouter"
    if OPENAI_API_KEY:
        return "openai"
    return "none"


def _resolve_model(provider: str, override: str = "") -> str:
    model = override or DEFAULT_MODEL
    if model:
        return model
    if provider == "openrouter":
        return "openrouter/auto"
    if provider == "openai":
        return "gpt-4o-mini"
    return ""


def _build_client(provider: str) -> AsyncOpenAI | None:
    if provider == "openrouter":
        return AsyncOpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
        )
    if provider == "openai":
        return AsyncOpenAI(api_key=OPENAI_API_KEY)
    return None


# ---------------------------------------------------------------------------
# Session / agent storage (in-memory MVP)
# ---------------------------------------------------------------------------

# sessions: {session_id: {agents: {name: {system, model}}, active_agent: str, history: {agent_name: [msgs]}}}
sessions: dict[str, dict[str, Any]] = {}


def _default_agents() -> dict[str, dict[str, str]]:
    return {
        "Nova": {
            "system": CUSTOM_SYSTEM_PROMPT or NOVA_DEFAULT_SYSTEM,
            "model": "",
        }
    }


def _get_session(session_id: str) -> dict[str, Any]:
    if session_id not in sessions:
        sessions[session_id] = {
            "agents": _default_agents(),
            "active_agent": "Nova",
            "history": {"Nova": []},
        }
    return sessions[session_id]


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "****" if key else ""
    return key[:4] + "•" * (len(key) - 8) + key[-4:]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    model: str = ""


class AgentCreateRequest(BaseModel):
    name: str
    system: str = ""
    model: str = ""


class AgentSwitchRequest(BaseModel):
    name: str


class SettingsUpdate(BaseModel):
    openrouter_api_key: str | None = None
    openai_api_key: str | None = None
    default_model: str | None = None
    system_prompt: str | None = None


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Nova Web", version="1.0.0")

# Serve static files
_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ---------------------------------------------------------------------------
# Middleware — session cookie
# ---------------------------------------------------------------------------

SESSION_COOKIE = "nova_session"


@app.middleware("http")
async def session_middleware(request: Request, call_next):
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        sid = str(uuid.uuid4())
    request.state.session_id = sid
    response: Response = await call_next(request)
    response.set_cookie(
        SESSION_COOKIE, sid, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax"
    )
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root():
    index = _static_dir / "index.html"
    if index.is_file():
        return HTMLResponse(index.read_text())
    return HTMLResponse("<h1>Nova Web</h1><p>static/index.html not found.</p>")


@app.get("/api/health")
async def health():
    provider = _resolve_provider()
    model = _resolve_model(provider)
    return {
        "status": "ok",
        "provider": provider,
        "model": model,
        "uptime_seconds": round(time.time() - BOOT_TIME, 1),
        "has_api_key": provider != "none",
    }


@app.get("/api/settings")
async def get_settings():
    return {
        "openrouter_api_key": _mask_key(OPENROUTER_API_KEY),
        "openai_api_key": _mask_key(OPENAI_API_KEY),
        "default_model": DEFAULT_MODEL,
        "system_prompt": CUSTOM_SYSTEM_PROMPT or NOVA_DEFAULT_SYSTEM,
        "active_provider": _resolve_provider(),
    }


@app.post("/api/settings")
async def update_settings(body: SettingsUpdate):
    global OPENROUTER_API_KEY, OPENAI_API_KEY, DEFAULT_MODEL, CUSTOM_SYSTEM_PROMPT

    if body.openrouter_api_key is not None:
        OPENROUTER_API_KEY = body.openrouter_api_key
        os.environ["OPENROUTER_API_KEY"] = body.openrouter_api_key
    if body.openai_api_key is not None:
        OPENAI_API_KEY = body.openai_api_key
        os.environ["OPENAI_API_KEY"] = body.openai_api_key
    if body.default_model is not None:
        DEFAULT_MODEL = body.default_model
    if body.system_prompt is not None:
        CUSTOM_SYSTEM_PROMPT = body.system_prompt
        # Update the default Nova agent system prompt in all sessions
        for s in sessions.values():
            if "Nova" in s["agents"]:
                s["agents"]["Nova"]["system"] = body.system_prompt

    return await get_settings()


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

@app.get("/api/agents")
async def list_agents(request: Request):
    sess = _get_session(request.state.session_id)
    return {
        "agents": list(sess["agents"].keys()),
        "active": sess["active_agent"],
    }


@app.post("/api/agents")
async def create_agent(request: Request, body: AgentCreateRequest):
    sess = _get_session(request.state.session_id)
    name = body.name.strip()
    if not name:
        return JSONResponse({"error": "Agent name is required"}, 400)
    sess["agents"][name] = {
        "system": body.system or f"You are {name}, a helpful assistant.",
        "model": body.model,
    }
    sess["history"].setdefault(name, [])
    sess["active_agent"] = name
    return {"agents": list(sess["agents"].keys()), "active": name}


@app.post("/api/agents/switch")
async def switch_agent(request: Request, body: AgentSwitchRequest):
    sess = _get_session(request.state.session_id)
    name = body.name.strip()
    if name not in sess["agents"]:
        return JSONResponse({"error": f"Agent '{name}' not found"}, 404)
    sess["active_agent"] = name
    return {"active": name, "history": sess["history"].get(name, [])}


@app.delete("/api/agents/{name}")
async def delete_agent(request: Request, name: str):
    sess = _get_session(request.state.session_id)
    if name == "Nova":
        return JSONResponse({"error": "Cannot delete the default Nova agent"}, 400)
    if name not in sess["agents"]:
        return JSONResponse({"error": f"Agent '{name}' not found"}, 404)
    del sess["agents"][name]
    sess["history"].pop(name, None)
    if sess["active_agent"] == name:
        sess["active_agent"] = "Nova"
    return {"agents": list(sess["agents"].keys()), "active": sess["active_agent"]}


# ---------------------------------------------------------------------------
# Chat (streaming SSE)
# ---------------------------------------------------------------------------

@app.post("/api/chat")
async def chat(request: Request, body: ChatRequest):
    provider = _resolve_provider()
    if provider == "none":
        return JSONResponse(
            {"error": "No API key configured. Go to Settings to add one."}, 400
        )

    sess = _get_session(request.state.session_id)
    agent_name = sess["active_agent"]
    agent = sess["agents"][agent_name]
    model = _resolve_model(provider, body.model or agent.get("model", ""))
    system_prompt = agent.get("system", NOVA_DEFAULT_SYSTEM)

    # Append user message to history
    history = sess["history"].setdefault(agent_name, [])
    history.append({"role": "user", "content": body.message})

    # Keep last 50 messages to limit context
    if len(history) > 50:
        history[:] = history[-50:]

    messages = [{"role": "system", "content": system_prompt}] + history

    client = _build_client(provider)
    if not client:
        return JSONResponse({"error": "Failed to build LLM client"}, 500)

    async def event_stream() -> AsyncGenerator[dict[str, str], None]:
        full_response = ""
        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                max_tokens=4096,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    full_response += delta.content
                    yield {"event": "token", "data": json.dumps({"token": delta.content})}

            # Store assistant response in history
            history.append({"role": "assistant", "content": full_response})
            yield {
                "event": "done",
                "data": json.dumps({"model": model, "provider": provider}),
            }
        except Exception as exc:
            error_msg = str(exc)
            yield {"event": "error", "data": json.dumps({"error": error_msg})}

    return EventSourceResponse(event_stream())


@app.post("/api/chat/clear")
async def clear_chat(request: Request):
    sess = _get_session(request.state.session_id)
    agent_name = sess["active_agent"]
    sess["history"][agent_name] = []
    return {"cleared": True, "agent": agent_name}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=False)
