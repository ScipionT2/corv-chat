"""
Nova Web — Standalone FastAPI backend.

Multi-provider LLM chat (OpenRouter + OpenAI) with streaming SSE,
Google OAuth login, SQLite-based persistent storage for users/chats/agents,
custom agents, and settings management.

Run:  uvicorn server:app --host 0.0.0.0 --port 8766
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import aiosqlite
from authlib.integrations.starlette_client import OAuth
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from openai import AsyncOpenAI
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.sessions import SessionMiddleware

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv(Path.cwd() / ".env")

OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
DEFAULT_MODEL: str = os.getenv("NOVA_DEFAULT_MODEL", "")
PORT: int = int(os.getenv("PORT", "8766"))
CUSTOM_SYSTEM_PROMPT: str = os.getenv("NOVA_SYSTEM_PROMPT", "")

APP_SECRET_KEY: str = os.getenv("APP_SECRET_KEY", "change-me-to-random-string")
GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI: str = os.getenv(
    "GOOGLE_REDIRECT_URI", f"http://localhost:{PORT}/auth/callback"
)
DB_PATH: str = os.getenv("NOVA_DB_PATH", "nova.db")

BOOT_TIME: float = time.time()

# ---------------------------------------------------------------------------
# Signing / cookies
# ---------------------------------------------------------------------------

_signer = URLSafeTimedSerializer(APP_SECRET_KEY)
AUTH_COOKIE = "nova_auth"
ANON_COOKIE = "nova_anon"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

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


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "****" if key else ""
    return key[:4] + "•" * (len(key) - 8) + key[-4:]


def _oauth_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
    return _db


async def init_db() -> None:
    db = await get_db()
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            avatar TEXT,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            system_prompt TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            UNIQUE(user_id, name)
        );

        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL REFERENCES users(id),
            agent_name TEXT NOT NULL DEFAULT 'Nova',
            title TEXT NOT NULL DEFAULT 'New Chat',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        """
    )
    await db.commit()


async def ensure_user(user_id: str, email: str, name: str, avatar: str | None = None) -> None:
    """Insert user if not exists, update name/avatar if changed."""
    db = await get_db()
    now = time.time()
    await db.execute(
        """
        INSERT INTO users (id, email, name, avatar, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name, avatar=excluded.avatar
        """,
        (user_id, email, name, avatar, now),
    )
    await db.commit()


async def ensure_default_agent(user_id: str) -> None:
    """Create the default Nova agent for a user if it doesn't exist."""
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT id FROM agents WHERE user_id=? AND name='Nova'", (user_id,)
    )
    if not row:
        now = time.time()
        await db.execute(
            "INSERT INTO agents (user_id, name, system_prompt, model, created_at) VALUES (?,?,?,?,?)",
            (user_id, "Nova", CUSTOM_SYSTEM_PROMPT or NOVA_DEFAULT_SYSTEM, "", now),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _sign_cookie(user_id: str) -> str:
    return _signer.dumps(user_id)


def _unsign_cookie(value: str, max_age: int = COOKIE_MAX_AGE) -> str | None:
    try:
        return _signer.loads(value, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


async def _get_user_id(request: Request) -> str | None:
    """Resolve the authenticated or anonymous user id from cookies."""
    # Try auth cookie first
    auth_val = request.cookies.get(AUTH_COOKIE)
    if auth_val:
        uid = _unsign_cookie(auth_val)
        if uid:
            return uid

    # Try anonymous cookie
    anon_val = request.cookies.get(ANON_COOKIE)
    if anon_val:
        uid = _unsign_cookie(anon_val)
        if uid:
            return uid

    return None


async def _get_or_create_user(request: Request, response_setter=None) -> tuple[str, bool]:
    """Get user id or create anonymous user. Returns (user_id, is_new)."""
    uid = await _get_user_id(request)
    if uid:
        return uid, False

    # Create anonymous user
    uid = f"anon_{uuid.uuid4().hex[:16]}"
    await ensure_user(uid, f"{uid}@anonymous", "Anonymous")
    await ensure_default_agent(uid)
    return uid, True


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    model: str = ""
    chat_id: Optional[int] = None


class AgentCreateRequest(BaseModel):
    name: str
    system_prompt: str = ""
    model: str = ""


class AgentSwitchRequest(BaseModel):
    name: str


class ChatCreateRequest(BaseModel):
    agent_name: str = "Nova"
    title: str = "New Chat"


class SettingsUpdate(BaseModel):
    openrouter_api_key: str | None = None
    openai_api_key: str | None = None
    default_model: str | None = None
    system_prompt: str | None = None


# ---------------------------------------------------------------------------
# In-memory session state (for active agent tracking)
# ---------------------------------------------------------------------------

_active_agents: dict[str, str] = {}  # user_id -> active_agent_name


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Nova Web", version="2.0.0")

# Session middleware (needed by authlib OAuth)
app.add_middleware(SessionMiddleware, secret_key=APP_SECRET_KEY)

# Serve static files
_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# ---------------------------------------------------------------------------
# OAuth setup
# ---------------------------------------------------------------------------

oauth = OAuth()
if _oauth_configured():
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def startup():
    await init_db()


@app.on_event("shutdown")
async def shutdown():
    global _db
    if _db:
        await _db.close()
        _db = None


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------


@app.get("/auth/login")
async def auth_login(request: Request):
    """Redirect to Google OAuth consent screen."""
    if not _oauth_configured():
        return JSONResponse(
            {"error": "Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."},
            400,
        )
    google = oauth.create_client("google")
    redirect_uri = GOOGLE_REDIRECT_URI
    return await google.authorize_redirect(request, redirect_uri)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    """Handle Google OAuth callback."""
    if not _oauth_configured():
        return JSONResponse({"error": "Google OAuth not configured"}, 400)

    try:
        google = oauth.create_client("google")
        token = await google.authorize_access_token(request)
        user_info = token.get("userinfo")
        if not user_info:
            return JSONResponse({"error": "Failed to get user info from Google"}, 400)

        google_id = user_info["sub"]
        email = user_info.get("email", "")
        name = user_info.get("name", email)
        avatar = user_info.get("picture", "")

        await ensure_user(google_id, email, name, avatar)
        await ensure_default_agent(google_id)

        response = RedirectResponse(url="/")
        response.set_cookie(
            AUTH_COOKIE,
            _sign_cookie(google_id),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        # Remove anon cookie if present
        response.delete_cookie(ANON_COOKIE)
        return response

    except Exception as exc:
        return JSONResponse({"error": f"OAuth callback failed: {exc}"}, 500)


@app.get("/auth/me")
async def auth_me(request: Request):
    """Return current user info or 401."""
    uid = await _get_user_id(request)
    if not uid:
        return JSONResponse({"error": "Not authenticated"}, 401)

    if uid.startswith("anon_"):
        return {"anonymous": True, "id": uid}

    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM users WHERE id=?", (uid,))
    if not rows:
        return JSONResponse({"error": "User not found"}, 401)

    row = rows[0]
    return {
        "anonymous": False,
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "avatar": row["avatar"],
    }


@app.post("/auth/logout")
async def auth_logout():
    """Clear session cookies."""
    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie(AUTH_COOKIE)
    response.delete_cookie(ANON_COOKIE)
    return response


# ---------------------------------------------------------------------------
# Middleware — ensure user exists for every API call
# ---------------------------------------------------------------------------


@app.middleware("http")
async def user_middleware(request: Request, call_next):
    """Ensure a user exists (authenticated or anonymous) for API routes."""
    # Skip for auth routes and static files
    path = request.url.path
    if path.startswith("/auth/") or path.startswith("/static/") or path == "/":
        return await call_next(request)

    uid = await _get_user_id(request)
    need_cookie = False
    if not uid:
        uid = f"anon_{uuid.uuid4().hex[:16]}"
        await ensure_user(uid, f"{uid}@anonymous", "Anonymous")
        await ensure_default_agent(uid)
        need_cookie = True

    request.state.user_id = uid
    response: Response = await call_next(request)

    if need_cookie:
        response.set_cookie(
            ANON_COOKIE,
            _sign_cookie(uid),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )

    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
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
        "oauth_configured": _oauth_configured(),
    }


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


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

    return await get_settings()


# ---------------------------------------------------------------------------
# Chats (persistent)
# ---------------------------------------------------------------------------


@app.get("/api/chats")
async def list_chats(request: Request):
    """List user's chats ordered by updated_at desc."""
    uid = request.state.user_id
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id, title, agent_name, created_at, updated_at FROM chats WHERE user_id=? ORDER BY updated_at DESC",
        (uid,),
    )
    return {
        "chats": [
            {
                "id": r["id"],
                "title": r["title"],
                "agent_name": r["agent_name"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    }


@app.post("/api/chats")
async def create_chat(request: Request, body: ChatCreateRequest):
    """Create a new chat."""
    uid = request.state.user_id
    db = await get_db()
    now = time.time()
    cursor = await db.execute(
        "INSERT INTO chats (user_id, agent_name, title, created_at, updated_at) VALUES (?,?,?,?,?)",
        (uid, body.agent_name, body.title, now, now),
    )
    await db.commit()
    chat_id = cursor.lastrowid
    return {
        "id": chat_id,
        "title": body.title,
        "agent_name": body.agent_name,
        "created_at": now,
        "updated_at": now,
    }


@app.delete("/api/chats/{chat_id}")
async def delete_chat(request: Request, chat_id: int):
    """Delete a chat and its messages."""
    uid = request.state.user_id
    db = await get_db()
    # Verify ownership
    rows = await db.execute_fetchall(
        "SELECT id FROM chats WHERE id=? AND user_id=?", (chat_id, uid)
    )
    if not rows:
        return JSONResponse({"error": "Chat not found"}, 404)
    await db.execute("DELETE FROM chats WHERE id=?", (chat_id,))
    await db.commit()
    return {"deleted": True, "chat_id": chat_id}


@app.get("/api/chats/{chat_id}/messages")
async def get_chat_messages(request: Request, chat_id: int):
    """Get all messages for a chat."""
    uid = request.state.user_id
    db = await get_db()
    # Verify ownership
    rows = await db.execute_fetchall(
        "SELECT id FROM chats WHERE id=? AND user_id=?", (chat_id, uid)
    )
    if not rows:
        return JSONResponse({"error": "Chat not found"}, 404)

    msgs = await db.execute_fetchall(
        "SELECT id, role, content, created_at FROM messages WHERE chat_id=? ORDER BY created_at ASC",
        (chat_id,),
    )
    return {
        "messages": [
            {
                "id": m["id"],
                "role": m["role"],
                "content": m["content"],
                "created_at": m["created_at"],
            }
            for m in msgs
        ]
    }


# ---------------------------------------------------------------------------
# Agents (persistent)
# ---------------------------------------------------------------------------


@app.get("/api/agents")
async def list_agents(request: Request):
    """List user's agents from DB."""
    uid = request.state.user_id
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id, name, system_prompt, model, created_at FROM agents WHERE user_id=? ORDER BY created_at ASC",
        (uid,),
    )
    active = _active_agents.get(uid, "Nova")
    return {
        "agents": [
            {
                "id": r["id"],
                "name": r["name"],
                "system_prompt": r["system_prompt"],
                "model": r["model"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
        "active": active,
    }


@app.post("/api/agents")
async def create_agent(request: Request, body: AgentCreateRequest):
    """Create a new agent in DB."""
    uid = request.state.user_id
    name = body.name.strip()
    if not name:
        return JSONResponse({"error": "Agent name is required"}, 400)

    db = await get_db()
    now = time.time()
    try:
        cursor = await db.execute(
            "INSERT INTO agents (user_id, name, system_prompt, model, created_at) VALUES (?,?,?,?,?)",
            (uid, name, body.system_prompt or f"You are {name}, a helpful assistant.", body.model, now),
        )
        await db.commit()
    except aiosqlite.IntegrityError:
        return JSONResponse({"error": f"Agent '{name}' already exists"}, 409)

    _active_agents[uid] = name
    return {
        "id": cursor.lastrowid,
        "name": name,
        "system_prompt": body.system_prompt,
        "model": body.model,
        "created_at": now,
    }


@app.delete("/api/agents/{name}")
async def delete_agent(request: Request, name: str):
    """Delete an agent from DB."""
    uid = request.state.user_id
    if name == "Nova":
        return JSONResponse({"error": "Cannot delete the default Nova agent"}, 400)

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id FROM agents WHERE user_id=? AND name=?", (uid, name)
    )
    if not rows:
        return JSONResponse({"error": f"Agent '{name}' not found"}, 404)

    await db.execute("DELETE FROM agents WHERE user_id=? AND name=?", (uid, name))
    await db.commit()

    if _active_agents.get(uid) == name:
        _active_agents[uid] = "Nova"

    return {"deleted": True, "name": name, "active": _active_agents.get(uid, "Nova")}


@app.post("/api/agents/switch")
async def switch_agent(request: Request, body: AgentSwitchRequest):
    """Switch the active agent."""
    uid = request.state.user_id
    name = body.name.strip()

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id FROM agents WHERE user_id=? AND name=?", (uid, name)
    )
    if not rows:
        return JSONResponse({"error": f"Agent '{name}' not found"}, 404)

    _active_agents[uid] = name
    return {"active": name}


# ---------------------------------------------------------------------------
# Chat (streaming SSE) — persistent
# ---------------------------------------------------------------------------


@app.post("/api/chat")
async def chat(request: Request, body: ChatRequest):
    """Stream a chat response via SSE. Persists messages to DB."""
    uid = request.state.user_id
    provider = _resolve_provider()
    if provider == "none":
        return JSONResponse(
            {"error": "No API key configured. Go to Settings to add one."}, 400
        )

    db = await get_db()
    chat_id = body.chat_id
    active_agent = _active_agents.get(uid, "Nova")

    # Auto-create chat if no chat_id provided
    if not chat_id:
        now = time.time()
        # Use first 50 chars of message as title
        title = body.message[:50].strip() or "New Chat"
        cursor = await db.execute(
            "INSERT INTO chats (user_id, agent_name, title, created_at, updated_at) VALUES (?,?,?,?,?)",
            (uid, active_agent, title, now, now),
        )
        await db.commit()
        chat_id = cursor.lastrowid
    else:
        # Verify ownership
        rows = await db.execute_fetchall(
            "SELECT id, agent_name FROM chats WHERE id=? AND user_id=?", (chat_id, uid)
        )
        if not rows:
            return JSONResponse({"error": "Chat not found"}, 404)
        active_agent = rows[0]["agent_name"]

    # Get agent config
    agent_rows = await db.execute_fetchall(
        "SELECT system_prompt, model FROM agents WHERE user_id=? AND name=?",
        (uid, active_agent),
    )
    if agent_rows:
        agent_system = agent_rows[0]["system_prompt"] or NOVA_DEFAULT_SYSTEM
        agent_model = agent_rows[0]["model"] or ""
    else:
        agent_system = NOVA_DEFAULT_SYSTEM
        agent_model = ""

    model = _resolve_model(provider, body.model or agent_model)
    system_prompt = agent_system

    # Save user message to DB
    now = time.time()
    await db.execute(
        "INSERT INTO messages (chat_id, role, content, created_at) VALUES (?,?,?,?)",
        (chat_id, "user", body.message, now),
    )
    await db.execute(
        "UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id)
    )
    await db.commit()

    # Load chat history from DB (last 50 messages)
    history_rows = await db.execute_fetchall(
        "SELECT role, content FROM messages WHERE chat_id=? ORDER BY created_at ASC",
        (chat_id,),
    )
    history = [{"role": r["role"], "content": r["content"]} for r in history_rows]
    # Keep last 50
    if len(history) > 50:
        history = history[-50:]

    messages = [{"role": "system", "content": system_prompt}] + history

    client = _build_client(provider)
    if not client:
        return JSONResponse({"error": "Failed to build LLM client"}, 500)

    # Capture chat_id for the SSE closure
    _chat_id = chat_id

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

            # Save assistant response to DB
            save_time = time.time()
            save_db = await get_db()
            await save_db.execute(
                "INSERT INTO messages (chat_id, role, content, created_at) VALUES (?,?,?,?)",
                (_chat_id, "assistant", full_response, save_time),
            )
            await save_db.execute(
                "UPDATE chats SET updated_at=? WHERE id=?", (save_time, _chat_id)
            )
            await save_db.commit()

            yield {
                "event": "done",
                "data": json.dumps({
                    "model": model,
                    "provider": provider,
                    "chat_id": _chat_id,
                }),
            }
        except Exception as exc:
            error_msg = str(exc)
            yield {"event": "error", "data": json.dumps({"error": error_msg})}

    return EventSourceResponse(event_stream())


@app.post("/api/chat/clear")
async def clear_chat(request: Request, body: dict | None = None):
    """Clear messages from a chat (or all chats for user if no chat_id)."""
    uid = request.state.user_id
    db = await get_db()

    chat_id = (body or {}).get("chat_id")
    if chat_id:
        # Verify ownership
        rows = await db.execute_fetchall(
            "SELECT id FROM chats WHERE id=? AND user_id=?", (chat_id, uid)
        )
        if not rows:
            return JSONResponse({"error": "Chat not found"}, 404)
        await db.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
        await db.commit()
        return {"cleared": True, "chat_id": chat_id}
    else:
        # Clear all user's chats' messages
        chat_rows = await db.execute_fetchall(
            "SELECT id FROM chats WHERE user_id=?", (uid,)
        )
        for cr in chat_rows:
            await db.execute("DELETE FROM messages WHERE chat_id=?", (cr["id"],))
        await db.commit()
        return {"cleared": True, "all": True}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=False)
