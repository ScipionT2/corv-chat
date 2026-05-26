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
import logging

logger = logging.getLogger("nova")

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
# same_site="none" is required so the session cookie survives the cross-origin
# redirect from accounts.google.com back to nov-assistant.com/auth/callback.
# secure=True (HTTPS-only) keeps it safe.
app.add_middleware(
    SessionMiddleware,
    secret_key=APP_SECRET_KEY,
    https_only=True,
    same_site="none",
    max_age=14 * 24 * 60 * 60,  # 14 days
)

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
    import traceback as _tb

    if not _oauth_configured():
        return JSONResponse({"error": "Google OAuth not configured"}, 400)

    # Check for error from Google (user denied, etc.)
    error = request.query_params.get("error")
    if error:
        error_desc = request.query_params.get("error_description", "Unknown error")
        logger.warning(f"OAuth error from Google: {error} — {error_desc}")
        # Redirect to home with error flag instead of showing raw JSON
        return RedirectResponse(url=f"/?auth_error={error}")

    try:
        google = oauth.create_client("google")
        token = await google.authorize_access_token(request)
        user_info = token.get("userinfo")
        if not user_info:
            logger.error("OAuth callback: token received but no userinfo")
            return RedirectResponse(url="/?auth_error=no_userinfo")

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
            secure=True,
            samesite="lax",
        )
        # Remove anon cookie if present
        response.delete_cookie(ANON_COOKIE)
        return response

    except Exception as exc:
        logger.error(f"OAuth callback failed: {exc}\n{_tb.format_exc()}")
        # State mismatch = session cookie lost; redirect to retry instead of 500
        if "mismatching_state" in str(exc) or "CSRF" in str(exc):
            return RedirectResponse(url="/auth/login")
        return RedirectResponse(url=f"/?auth_error=callback_failed")


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
            secure=True,
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





# ===================================================================
#  Website Pages — served from /static/site/
# ===================================================================

@app.get("/site/{page}", response_class=HTMLResponse)
async def serve_site_page(page: str):
    """Serve marketing site pages."""
    import os
    safe_page = os.path.basename(page)
    if not safe_page.endswith(".html"):
        safe_page += ".html"
    path = _static_dir / "site" / safe_page
    if not path.exists():
        return HTMLResponse("<h1>Page not found</h1>", status_code=404)
    return HTMLResponse(path.read_text())


@app.get("/site", response_class=HTMLResponse)
async def serve_site_index():
    """Serve marketing site landing page."""
    path = _static_dir / "site" / "index.html"
    if path.exists():
        return HTMLResponse(path.read_text())
    return HTMLResponse("<h1>Coming soon</h1>", status_code=404)


# ===================================================================
#  Image Generation - Free via Pollinations.ai
# ===================================================================

class ImageGenRequest(BaseModel):
    prompt: str
    width: int = 1024
    height: int = 1024
    model: str = "flux"
    seed: int | None = None
    enhance: bool = True
    nologo: bool = True

IMAGE_MODELS = {
    "flux": {"name": "Flux (Default)", "provider": "Pollinations", "free": True},
    "flux-realism": {"name": "Flux Realism", "provider": "Pollinations", "free": True},
    "flux-anime": {"name": "Flux Anime", "provider": "Pollinations", "free": True},
    "flux-3d": {"name": "Flux 3D", "provider": "Pollinations", "free": True},
    "turbo": {"name": "Turbo (Fast)", "provider": "Pollinations", "free": True},
    "dall-e-3": {"name": "DALL-E 3", "provider": "OpenAI", "free": False},
    "gpt-image-2": {"name": "GPT Image 2", "provider": "OpenAI", "free": False},
}


@app.get("/api/v2/image-models")
async def get_image_models():
    return {"models": [{
        "id": k, "name": v["name"], "provider": v["provider"], "free": v["free"]
    } for k, v in IMAGE_MODELS.items()]}


@app.post("/api/v2/image")
async def generate_image(request: Request, body: ImageGenRequest):
    import urllib.parse

    if body.model in ("dall-e-3", "gpt-image-2"):
        return JSONResponse({"error": "Premium image models require an API key (coming soon)."}, 400)

    encoded = urllib.parse.quote(body.prompt)
    params = f"width={body.width}&height={body.height}&nologo=true"
    if body.model != "flux":
        params += f"&model={body.model}"
    if body.seed is not None:
        params += f"&seed={body.seed}"
    if body.enhance:
        params += "&enhance=true"

    image_url = f"https://image.pollinations.ai/prompt/{encoded}?{params}"

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.head(image_url)
            if resp.status_code != 200:
                return JSONResponse({"error": "Image generation failed. Try again."}, 500)
    except Exception as e:
        return JSONResponse({"error": f"Image service error: {str(e)[:100]}"}, 500)

    return {"image_url": image_url, "model": body.model, "prompt": body.prompt}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=False)

# ═══════════════════════════════════════════════════════════════════════
#  V2 API — Multi-Provider Chat (Free + BYOK)
# ═══════════════════════════════════════════════════════════════════════

import httpx
# logger already defined at top

GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
SAMBANOVA_API_KEY: str = os.getenv("SAMBANOVA_API_KEY", "")
OPENROUTER_FREE_KEY: str = os.getenv("OPENROUTER_API_KEY", "")

V2_MODELS = {
    # ── FREE: each model has fallbacks across providers ─────────────
    # provider = primary provider, fallbacks = list of (provider, model_id, api_key_env)
    "nova-auto": {
        "provider": "groq", "name": "Nova Auto (Best Available)",
        "model_id": "llama-3.3-70b-versatile", "free": True,
        "fallbacks": [
            ("sambanova", "DeepSeek-V3.1", "SAMBANOVA_API_KEY"),
            ("openrouter-free", "openrouter/free", "OPENROUTER_API_KEY"),
            ("openrouter-free", "deepseek/deepseek-v4-flash:free", "OPENROUTER_API_KEY"),
        ],
    },
    "deepseek-v4-flash": {
        "provider": "openrouter-free", "name": "DeepSeek V4 Flash",
        "model_id": "deepseek/deepseek-v4-flash:free", "free": True,
        "fallbacks": [
            ("sambanova", "DeepSeek-V3.1", "SAMBANOVA_API_KEY"),
            ("groq", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
        ],
    },
    "gpt-oss-120b": {
        "provider": "openrouter-free", "name": "GPT-OSS 120B",
        "model_id": "openai/gpt-oss-120b:free", "free": True,
        "fallbacks": [
            ("groq", "openai/gpt-oss-120b", "GROQ_API_KEY"),
            ("sambanova", "gpt-oss-120b", "SAMBANOVA_API_KEY"),
        ],
    },
    "llama-3.3-70b": {
        "provider": "groq", "name": "Llama 3.3 70B",
        "model_id": "llama-3.3-70b-versatile", "free": True,
        "fallbacks": [
            ("openrouter-free", "meta-llama/llama-3.3-70b-instruct:free", "OPENROUTER_API_KEY"),
            ("sambanova", "Meta-Llama-3.3-70B-Instruct", "SAMBANOVA_API_KEY"),
        ],
    },
    "qwen3-coder": {
        "provider": "openrouter-free", "name": "Qwen3 Coder 480B",
        "model_id": "qwen/qwen3-coder:free", "free": True,
        "fallbacks": [
            ("groq", "qwen/qwen3-32b", "GROQ_API_KEY"),
            ("sambanova", "QwQ-32B", "SAMBANOVA_API_KEY"),
        ],
    },
    "qwen3-next-80b": {
        "provider": "openrouter-free", "name": "Qwen3 Next 80B",
        "model_id": "qwen/qwen3-next-80b-a3b-instruct:free", "free": True,
        "fallbacks": [
            ("groq", "qwen/qwen3-32b", "GROQ_API_KEY"),
            ("sambanova", "QwQ-32B", "SAMBANOVA_API_KEY"),
        ],
    },
    "nemotron-3-super": {
        "provider": "openrouter-free", "name": "Nemotron 3 Super 120B",
        "model_id": "nvidia/nemotron-3-super-120b-a12b:free", "free": True,
        "fallbacks": [
            ("groq", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
            ("sambanova", "DeepSeek-V3.1", "SAMBANOVA_API_KEY"),
        ],
    },
    "nemotron-3-nano": {
        "provider": "openrouter-free", "name": "Nemotron 3 Nano 30B",
        "model_id": "nvidia/nemotron-3-nano-30b-a3b:free", "free": True,
        "fallbacks": [
            ("groq", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
            ("sambanova", "DeepSeek-V3.1", "SAMBANOVA_API_KEY"),
        ],
    },
    "gemma-4-31b": {
        "provider": "openrouter-free", "name": "Gemma 4 31B",
        "model_id": "google/gemma-4-31b-it:free", "free": True,
        "fallbacks": [
            ("groq", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
            ("sambanova", "DeepSeek-V3.1", "SAMBANOVA_API_KEY"),
        ],
    },
    "minimax-m2.5": {
        "provider": "openrouter-free", "name": "MiniMax M2.5",
        "model_id": "minimax/minimax-m2.5:free", "free": True,
        "fallbacks": [
            ("sambanova", "MiniMax-M2.7", "SAMBANOVA_API_KEY"),
            ("groq", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
        ],
    },
    "glm-4.5-air": {
        "provider": "openrouter-free", "name": "GLM 4.5 Air",
        "model_id": "z-ai/glm-4.5-air:free", "free": True,
        "fallbacks": [
            ("groq", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
            ("sambanova", "DeepSeek-V3.1", "SAMBANOVA_API_KEY"),
        ],
    },
    "trinity-thinking": {
        "provider": "openrouter-free", "name": "Trinity Large Thinking",
        "model_id": "arcee-ai/trinity-large-thinking:free", "free": True,
        "fallbacks": [
            ("groq", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
            ("sambanova", "DeepSeek-V3.1", "SAMBANOVA_API_KEY"),
        ],
    },
    "laguna-m1": {
        "provider": "openrouter-free", "name": "Laguna M.1",
        "model_id": "poolside/laguna-m.1:free", "free": True,
        "fallbacks": [
            ("groq", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
            ("sambanova", "DeepSeek-V3.1", "SAMBANOVA_API_KEY"),
        ],
    },
    "hermes-3-405b": {
        "provider": "openrouter-free", "name": "Hermes 3 405B",
        "model_id": "nousresearch/hermes-3-llama-3.1-405b:free", "free": True,
        "fallbacks": [
            ("groq", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
            ("sambanova", "DeepSeek-V3.1", "SAMBANOVA_API_KEY"),
        ],
    },
    # ── BYOK (user provides own key, no fallback) ──────────────────
    "gpt-5.5-pro": {"provider": "openai", "name": "GPT-5.5 Pro", "model_id": "gpt-5.5-pro", "free": False},
    "gpt-5.5": {"provider": "openai", "name": "GPT-5.5", "model_id": "gpt-5.5", "free": False},
    "gpt-4.1": {"provider": "openai", "name": "GPT-4.1", "model_id": "gpt-4.1", "free": False},
    "gpt-4o": {"provider": "openai", "name": "GPT-4o", "model_id": "gpt-4o", "free": False},
    "o3-mini": {"provider": "openai", "name": "o3 Mini", "model_id": "o3-mini", "free": False},
    "claude-opus-4.7": {"provider": "anthropic", "name": "Claude Opus 4.7", "model_id": "claude-opus-4-7", "free": False},
    "claude-sonnet-4.6": {"provider": "anthropic", "name": "Claude Sonnet 4.6", "model_id": "claude-sonnet-4-6", "free": False},
    "claude-sonnet": {"provider": "anthropic", "name": "Claude Sonnet 4", "model_id": "claude-sonnet-4-20250514", "free": False},
    "claude-haiku": {"provider": "anthropic", "name": "Claude Haiku 3.5", "model_id": "claude-3-5-haiku-20241022", "free": False},
}

@app.get("/api/v2/models")
async def v2_get_models():
    result = []
    for key, info in V2_MODELS.items():
        available = True
        if info["free"]:
            available = bool(OPENROUTER_FREE_KEY or GROQ_API_KEY or SAMBANOVA_API_KEY)
        else:
            available = False  # needs user key
        result.append({
            "id": key, "name": info["name"], "provider": info["provider"],
            "free": info["free"], "available": available,
        })
    return {"models": result}


class ChatV2Request(BaseModel):
    message: str
    model: str = "nova-auto"
    history: list[dict] = []
    api_key: str | None = None  # BYOK — never stored
    chat_id: Optional[int] = None
    agent: str | None = None          # active agent name
    workspace_prefix: str | None = None  # workspace context to prepend


async def _stream_openai_compat(messages, model_id, api_key, base_url="https://api.openai.com/v1"):
    url = f"{base_url}/chat/completions"
    payload = {"model": model_id, "messages": messages, "stream": True, "temperature": 0.7, "max_tokens": 4096}
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise Exception(f"API error {resp.status_code}: {body.decode()[:200]}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "): continue
                d = line[6:]
                if d.strip() == "[DONE]": break
                try:
                    data = json.loads(d)
                    text = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if text: yield text
                except json.JSONDecodeError: continue


async def _stream_gemini(messages, model_id, api_key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:streamGenerateContent"
    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})
    payload = {"contents": contents, "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096}}
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", url, params={"key": api_key, "alt": "sse"}, json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise Exception(f"Gemini error {resp.status_code}: {body.decode()[:200]}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "): continue
                d = line[6:]
                if d.strip() == "[DONE]": break
                try:
                    data = json.loads(d)
                    for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", []):
                        text = part.get("text", "")
                        if text: yield text
                except json.JSONDecodeError: continue


async def _stream_groq(messages, model_id, api_key):
    async for token in _stream_openai_compat(messages, model_id, api_key, "https://api.groq.com/openai/v1"):
        yield token


async def _stream_openrouter_free(messages, model_id, api_key):
    """Stream via OpenRouter with fallback routing for free models."""
    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": model_id,
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
        "max_tokens": 4096,
        "route": "fallback",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://nov-assistant.com",
        "X-Title": "Nova AI",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise Exception(f"OpenRouter error {resp.status_code}: {body.decode()[:300]}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "): continue
                d = line[6:]
                if d.strip() == "[DONE]": break
                try:
                    data = json.loads(d)
                    text = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if text: yield text
                except json.JSONDecodeError: continue


async def _stream_sambanova(messages, model_id, api_key):
    async for token in _stream_openai_compat(messages, model_id, api_key, "https://api.sambanova.ai/v1"):
        yield token


async def _stream_anthropic(messages, model_id, api_key):
    url = "https://api.anthropic.com/v1/messages"
    system = ""
    chat_msgs = []
    for msg in messages:
        if msg["role"] == "system": system = msg["content"]
        else: chat_msgs.append({"role": msg["role"], "content": msg["content"]})
    payload = {"model": model_id, "messages": chat_msgs, "max_tokens": 4096, "stream": True}
    if system: payload["system"] = system
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise Exception(f"Anthropic error {resp.status_code}: {body.decode()[:200]}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "): continue
                try:
                    data = json.loads(line[6:])
                    if data.get("type") == "content_block_delta":
                        text = data.get("delta", {}).get("text", "")
                        if text: yield text
                except json.JSONDecodeError: continue


def _get_stream(prov, model_id, api_key, messages):
    """Return the right async generator for a provider."""
    if prov == "openrouter-free":
        return _stream_openrouter_free(messages, model_id, api_key)
    elif prov == "groq":
        return _stream_groq(messages, model_id, api_key)
    elif prov == "sambanova":
        return _stream_sambanova(messages, model_id, api_key)
    elif prov == "gemini":
        return _stream_gemini(messages, model_id, api_key)
    elif prov == "openai":
        return _stream_openai_compat(messages, model_id, api_key)
    elif prov == "anthropic":
        return _stream_anthropic(messages, model_id, api_key)
    elif prov == "openrouter":
        return _stream_openai_compat(messages, model_id, api_key, "https://openrouter.ai/api/v1")
    return None


@app.post("/api/v2/chat")
async def v2_chat(request: Request, body: ChatV2Request):
    """Multi-provider chat with SSE streaming.
    Free models use server-side keys. BYOK models require api_key in body.
    API keys are NEVER stored — used for this request only, then discarded.
    """
    model_key = body.model
    if model_key not in V2_MODELS:
        return JSONResponse({"error": f"Unknown model: {model_key}"}, 400)

    info = V2_MODELS[model_key]
    provider = info["provider"]
    model_id = info["model_id"]

    # Resolve API key based on primary provider
    if info["free"]:
        _KEY_MAP = {
            "openrouter-free": OPENROUTER_FREE_KEY,
            "groq": GROQ_API_KEY,
            "sambanova": SAMBANOVA_API_KEY,
            "gemini": GEMINI_API_KEY,
        }
        api_key = _KEY_MAP.get(provider, OPENROUTER_FREE_KEY)
        if not api_key:
            # Try any available key as fallback
            api_key = OPENROUTER_FREE_KEY or GROQ_API_KEY or SAMBANOVA_API_KEY
        if not api_key:
            return JSONResponse({"error": f"No server key available"}, 500)
    else:
        api_key = body.api_key or ""
        if not api_key:
            return JSONResponse({"error": f"{info['name']} requires an API key. Add yours in Settings."}, 400)

    # --- Persist to DB (same pattern as v1 /api/chat) ---
    uid = request.state.user_id
    db = await get_db()
    now = time.time()

    chat_id = body.chat_id
    agent_name = body.agent or _active_agents.get(uid, "Nova")

    if not chat_id:
        title = body.message[:50].strip() or "New Chat"
        cursor = await db.execute(
            "INSERT INTO chats (user_id, agent_name, title, created_at, updated_at) VALUES (?,?,?,?,?)",
            (uid, agent_name, title, now, now),
        )
        await db.commit()
        chat_id = cursor.lastrowid
    else:
        rows = await db.execute_fetchall(
            "SELECT id, agent_name FROM chats WHERE id=? AND user_id=?", (chat_id, uid)
        )
        if not rows:
            return JSONResponse({"error": "Chat not found"}, 404)
        agent_name = rows[0]["agent_name"]

    # Save user message
    await db.execute(
        "INSERT INTO messages (chat_id, role, content, created_at) VALUES (?,?,?,?)",
        (chat_id, "user", body.message, now),
    )
    await db.execute("UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id))
    await db.commit()

    # Build messages from DB history (not client-side body.history)
    history_rows = await db.execute_fetchall(
        "SELECT role, content FROM messages WHERE chat_id=? ORDER BY created_at ASC",
        (chat_id,),
    )
    db_history = [{"role": r["role"], "content": r["content"]} for r in history_rows]
    # Keep last 50 messages for context
    if len(db_history) > 50:
        db_history = db_history[-50:]

    # Resolve agent system prompt (mirrors v1 /api/chat pattern)
    agent_rows = await db.execute_fetchall(
        "SELECT system_prompt, model FROM agents WHERE user_id=? AND name=?",
        (uid, agent_name),
    )
    if agent_rows and agent_rows[0]["system_prompt"]:
        base_system = agent_rows[0]["system_prompt"]
    else:
        base_system = CUSTOM_SYSTEM_PROMPT or NOVA_DEFAULT_SYSTEM

    # Prepend workspace prefix if provided
    if body.workspace_prefix:
        system_content = body.workspace_prefix + "\n\n" + base_system
    else:
        system_content = base_system

    system_msg = {"role": "system", "content": system_content}
    messages = [system_msg] + db_history

    async def _stream():
        full = ""
        used_provider = provider
        used_model = model_id

        # Build attempt list: primary + fallbacks
        attempts = [(provider, model_id, api_key)]
        for fb_prov, fb_model, fb_key_env in info.get("fallbacks", []):
            fb_key = os.environ.get(fb_key_env, "")
            if fb_key:
                attempts.append((fb_prov, fb_model, fb_key))

        last_error = None
        for attempt_prov, attempt_model, attempt_key in attempts:
            try:
                gen = _get_stream(attempt_prov, attempt_model, attempt_key, messages)
                if gen is None:
                    continue

                async for token in gen:
                    full += token
                    yield {"event": "token", "data": json.dumps({"token": token})}

                used_provider = attempt_prov
                used_model = attempt_model
                last_error = None
                break  # success — stop trying fallbacks

            except Exception as exc:
                last_error = exc
                logger.warning(f"Provider {attempt_prov}/{attempt_model} failed: {exc}")
                if full:
                    # Already sent partial tokens — can't retry cleanly
                    break
                continue  # try next fallback

        if last_error and not full:
            yield {"event": "error", "data": json.dumps({"error": f"All providers failed. Last error: {str(last_error)[:200]}"})}
            return

        if not full:
            yield {"event": "error", "data": json.dumps({"error": "No response from any provider."})}
            return

        # Save assistant response to DB
        save_time = time.time()
        save_db = await get_db()
        await save_db.execute(
            "INSERT INTO messages (chat_id, role, content, created_at) VALUES (?,?,?,?)",
            (chat_id, "assistant", full, save_time),
        )
        await save_db.execute(
            "UPDATE chats SET updated_at=? WHERE id=?", (save_time, chat_id)
        )
        await save_db.commit()

        yield {"event": "done", "data": json.dumps({"content": full, "model": model_key, "provider": used_provider, "chat_id": chat_id})}

    return EventSourceResponse(_stream())


# ---------------------------------------------------------------------------
# Offline Mode — Ollama Proxy
# ---------------------------------------------------------------------------

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")


class OfflineChatRequest(BaseModel):
    message: str
    model: str = ""
    chat_id: Optional[int] = None
    history: list[dict] = []


@app.get("/api/ollama/status")
async def ollama_status():
    """Check if Ollama is reachable."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = [m["name"] for m in data.get("models", [])]
                return {"online": True, "models": models, "url": OLLAMA_URL}
    except Exception:
        pass
    return {"online": False, "models": [], "url": OLLAMA_URL}


@app.post("/api/v2/chat/offline")
async def v2_chat_offline(request: Request, body: OfflineChatRequest):
    """Stream a chat response from local Ollama instance."""
    uid = request.state.user_id
    db = await get_db()
    now = time.time()
    model = body.model or OLLAMA_DEFAULT_MODEL

    # Check Ollama is reachable
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            if resp.status_code != 200:
                return JSONResponse({"error": "Ollama is not running. Start Ollama to use offline mode."}, 503)
    except Exception:
        return JSONResponse({"error": "Cannot reach Ollama at " + OLLAMA_URL + ". Make sure Ollama is running locally."}, 503)

    # Persist chat
    chat_id = body.chat_id
    if not chat_id:
        title = body.message[:50].strip() or "New Chat"
        cursor = await db.execute(
            "INSERT INTO chats (user_id, agent_name, title, created_at, updated_at) VALUES (?,?,?,?,?)",
            (uid, "Nova (Offline)", title, now, now),
        )
        await db.commit()
        chat_id = cursor.lastrowid
    else:
        rows = await db.execute_fetchall(
            "SELECT id FROM chats WHERE id=? AND user_id=?", (chat_id, uid)
        )
        if not rows:
            return JSONResponse({"error": "Chat not found"}, 404)

    # Save user message
    await db.execute(
        "INSERT INTO messages (chat_id, role, content, created_at) VALUES (?,?,?,?)",
        (chat_id, "user", body.message, now),
    )
    await db.execute("UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id))
    await db.commit()

    # Build history from DB
    history_rows = await db.execute_fetchall(
        "SELECT role, content FROM messages WHERE chat_id=? ORDER BY created_at ASC",
        (chat_id,),
    )
    db_history = [{"role": r["role"], "content": r["content"]} for r in history_rows]
    if len(db_history) > 50:
        db_history = db_history[-50:]

    system_msg = {"role": "system", "content": CUSTOM_SYSTEM_PROMPT or NOVA_DEFAULT_SYSTEM}
    messages = [system_msg] + db_history

    async def _stream():
        full = ""
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_URL}/api/chat",
                    json={"model": model, "messages": messages, "stream": True},
                ) as resp:
                    if resp.status_code != 200:
                        body_text = await resp.aread()
                        yield {"event": "error", "data": json.dumps({"error": f"Ollama error: {body_text.decode()[:200]}"})}
                        return
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                            token = chunk.get("message", {}).get("content", "")
                            if token:
                                full += token
                                yield {"event": "token", "data": json.dumps({"token": token})}
                            if chunk.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue
        except Exception as exc:
            if not full:
                yield {"event": "error", "data": json.dumps({"error": f"Ollama stream failed: {str(exc)[:200]}"})}
                return

        if not full:
            yield {"event": "error", "data": json.dumps({"error": "No response from Ollama."})}
            return

        # Save response
        save_time = time.time()
        save_db = await get_db()
        await save_db.execute(
            "INSERT INTO messages (chat_id, role, content, created_at) VALUES (?,?,?,?)",
            (chat_id, "assistant", full, save_time),
        )
        await save_db.execute("UPDATE chats SET updated_at=? WHERE id=?", (save_time, chat_id))
        await save_db.commit()

        yield {"event": "done", "data": json.dumps({"content": full, "model": model, "provider": "ollama", "chat_id": chat_id})}

    return EventSourceResponse(_stream())
