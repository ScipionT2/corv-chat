"""
Nova Web — Standalone FastAPI backend.

Multi-provider LLM chat (OpenRouter + OpenAI) with streaming SSE,
Google OAuth login, SQLite-based persistent storage for users/chats/agents,
custom agents, file uploads, conversation management, and settings.

Run:  uvicorn server:app --host 0.0.0.0 --port 8766
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import aiosqlite
from authlib.integrations.starlette_client import OAuth
from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request, Response, UploadFile, File, HTTPException
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from openai import AsyncOpenAI
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.sessions import SessionMiddleware

# Optional: Pillow for image thumbnails. Gracefully degrade if not installed.
try:
    from PIL import Image as PILImage
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

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

# Upload constants
UPLOAD_DIR: str = os.getenv("NOVA_UPLOAD_DIR", "/opt/nova-web/uploads")
MAX_UPLOAD_SIZE: int = 20 * 1024 * 1024  # 20 MB

ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
ALLOWED_DOC_TYPES = {
    "application/pdf", "text/plain", "text/markdown", "text/csv",
    "text/x-markdown",
}
ALLOWED_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp",
    "pdf", "txt", "md", "csv",
}

# ---------------------------------------------------------------------------
# Signing / cookies
# ---------------------------------------------------------------------------

_signer = URLSafeTimedSerializer(APP_SECRET_KEY)
AUTH_COOKIE = "nova_auth"
ANON_COOKIE = "nova_anon"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

# ---------------------------------------------------------------------------
# Popular Models (curated list for model selector)
# ---------------------------------------------------------------------------

POPULAR_MODELS = [
    {"id": "openrouter/auto", "name": "Auto (Best Available)", "category": "recommended"},
    {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet 4", "category": "recommended"},
    {"id": "openai/gpt-4o", "name": "GPT-4o", "category": "recommended"},
    {"id": "google/gemini-2.5-flash-preview", "name": "Gemini 2.5 Flash", "category": "recommended"},
    {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini", "category": "fast"},
    {"id": "google/gemini-2.0-flash-001", "name": "Gemini 2.0 Flash", "category": "fast"},
    {"id": "anthropic/claude-haiku-3.5", "name": "Claude 3.5 Haiku", "category": "fast"},
    {"id": "meta-llama/llama-4-maverick", "name": "Llama 4 Maverick", "category": "open"},
    {"id": "qwen/qwen3-235b-a22b", "name": "Qwen3 235B", "category": "open"},
    {"id": "deepseek/deepseek-r1", "name": "DeepSeek R1", "category": "reasoning"},
    {"id": "openai/o4-mini", "name": "o4 Mini", "category": "reasoning"},
    {"id": "anthropic/claude-opus-4", "name": "Claude Opus 4", "category": "premium"},
    {"id": "openai/o3", "name": "o3", "category": "premium"},
]

MODEL_CATEGORIES = [
    {"id": "recommended", "name": "Recommended", "icon": "⭐"},
    {"id": "fast", "name": "Fast", "icon": "⚡"},
    {"id": "open", "name": "Open Source", "icon": "🔓"},
    {"id": "reasoning", "name": "Reasoning", "icon": "🧠"},
    {"id": "premium", "name": "Premium", "icon": "💎"},
]

# ---------------------------------------------------------------------------
# Agent Templates (from agent patch)
# ---------------------------------------------------------------------------

AGENT_TEMPLATES = [
    {
        "name": "Coder",
        "description": "Expert programmer & debugger",
        "system_prompt": (
            "You are an expert software engineer proficient in Python, JavaScript, "
            "TypeScript, Rust, Go, and more. You write clean, efficient, well-documented "
            "code. You explain your reasoning, suggest best practices, and help debug "
            "issues methodically. When given a task, you think step-by-step and produce "
            "production-ready solutions."
        ),
        "model": "",
        "icon": "💻",
        "temperature": 0.4,
        "max_tokens": 4096,
        "top_p": 1.0,
    },
    {
        "name": "Writer",
        "description": "Creative writer & editor",
        "system_prompt": (
            "You are a skilled creative writer and editor. You craft engaging prose, "
            "compelling narratives, and polished copy. You adapt your tone — formal, "
            "casual, poetic, or technical — to match the request. You offer constructive "
            "feedback on writing, suggest improvements, and help with everything from "
            "emails to novels."
        ),
        "model": "",
        "icon": "✍️",
        "temperature": 0.9,
        "max_tokens": 4096,
        "top_p": 1.0,
    },
    {
        "name": "Tutor",
        "description": "Patient, knowledgeable teacher",
        "system_prompt": (
            "You are a patient, knowledgeable tutor who excels at breaking down complex "
            "topics into understandable pieces. You use analogies, examples, and step-by-step "
            "explanations. You check for understanding, ask guiding questions, and adapt your "
            "teaching style to the student's level. You cover any subject — math, science, "
            "history, languages, programming, and more."
        ),
        "model": "",
        "icon": "📚",
        "temperature": 0.6,
        "max_tokens": 4096,
        "top_p": 1.0,
    },
    {
        "name": "Analyst",
        "description": "Data & business analyst",
        "system_prompt": (
            "You are a sharp data and business analyst. You interpret data, identify trends, "
            "and provide actionable insights. You're proficient with statistics, financial "
            "modeling, market analysis, and data visualization concepts. You think critically, "
            "question assumptions, and present findings clearly with supporting evidence."
        ),
        "model": "",
        "icon": "📊",
        "temperature": 0.3,
        "max_tokens": 4096,
        "top_p": 1.0,
    },
    {
        "name": "Translator",
        "description": "Multilingual translator & interpreter",
        "system_prompt": (
            "You are an expert multilingual translator fluent in dozens of languages. "
            "You translate text accurately while preserving tone, context, idioms, and "
            "cultural nuances. You can handle formal documents, casual conversation, "
            "technical content, and literary works. When ambiguity exists, you explain "
            "alternative translations and cultural context."
        ),
        "model": "",
        "icon": "🌍",
        "temperature": 0.3,
        "max_tokens": 4096,
        "top_p": 1.0,
    },
    {
        "name": "Debater",
        "description": "Devil's advocate & critical thinker",
        "system_prompt": (
            "You are a skilled debater and critical thinker who challenges ideas "
            "constructively. You play devil's advocate, identify logical fallacies, "
            "and stress-test arguments from multiple angles. You present counter-arguments "
            "clearly and fairly. Your goal is to strengthen thinking, not to be contrarian — "
            "you help people see blind spots and refine their positions."
        ),
        "model": "",
        "icon": "⚔️",
        "temperature": 0.7,
        "max_tokens": 4096,
        "top_p": 1.0,
    },
]

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
# Upload helpers
# ---------------------------------------------------------------------------


def _make_thumbnail_base64(file_path: str, size: tuple = (96, 96)) -> str | None:
    """Generate a base64-encoded JPEG thumbnail for an image file."""
    if not HAS_PILLOW:
        return None
    try:
        with PILImage.open(file_path) as img:
            img.thumbnail(size, PILImage.LANCZOS)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=80)
            return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _read_file_base64(file_path: str) -> str:
    """Read an entire file and return base64-encoded content."""
    with open(file_path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _validate_upload(filename: str, content_type: str | None, size: int) -> str | None:
    """Validate upload constraints. Returns error message or None if OK."""
    if size > MAX_UPLOAD_SIZE:
        return f"File too large ({size // (1024*1024)}MB). Maximum is {MAX_UPLOAD_SIZE // (1024*1024)}MB."

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return f"Unsupported file type '.{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"

    all_allowed = ALLOWED_IMAGE_TYPES | ALLOWED_DOC_TYPES
    if content_type and content_type not in all_allowed:
        guessed, _ = mimetypes.guess_type(filename)
        if guessed and guessed not in all_allowed:
            return f"Unsupported MIME type '{content_type}'."

    return None


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

        CREATE TABLE IF NOT EXISTS uploads (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            original_name TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            size INTEGER NOT NULL,
            is_image INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
        """
    )
    # Add model column to messages if not present (migration)
    try:
        await db.execute("SELECT model FROM messages LIMIT 1")
    except Exception:
        await db.execute("ALTER TABLE messages ADD COLUMN model TEXT DEFAULT ''")
    await db.commit()


async def migrate_agents_table(db) -> None:
    """Add new columns to agents table (temperature, max_tokens, top_p, description)."""
    migrations = [
        ("temperature", "ALTER TABLE agents ADD COLUMN temperature REAL DEFAULT 0.7"),
        ("max_tokens", "ALTER TABLE agents ADD COLUMN max_tokens INTEGER DEFAULT 4096"),
        ("top_p", "ALTER TABLE agents ADD COLUMN top_p REAL DEFAULT 1.0"),
        ("description", "ALTER TABLE agents ADD COLUMN description TEXT DEFAULT ''"),
    ]
    for col_name, sql in migrations:
        try:
            await db.execute(sql)
        except Exception:
            pass  # Column already exists
    await db.commit()


async def migrate_chats_table() -> None:
    """Add pinned, archived, folder columns to chats table if they don't exist."""
    db = await get_db()
    cursor = await db.execute("PRAGMA table_info(chats)")
    columns = {row[1] for row in await cursor.fetchall()}

    if "pinned" not in columns:
        await db.execute("ALTER TABLE chats ADD COLUMN pinned INTEGER DEFAULT 0")
    if "archived" not in columns:
        await db.execute("ALTER TABLE chats ADD COLUMN archived INTEGER DEFAULT 0")
    if "folder" not in columns:
        await db.execute("ALTER TABLE chats ADD COLUMN folder TEXT DEFAULT ''")
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
    auth_val = request.cookies.get(AUTH_COOKIE)
    if auth_val:
        uid = _unsign_cookie(auth_val)
        if uid:
            return uid

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

    uid = f"anon_{uuid.uuid4().hex[:16]}"
    await ensure_user(uid, f"{uid}@anonymous", "Anonymous")
    await ensure_default_agent(uid)
    return uid, True


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ChatAttachment(BaseModel):
    file_id: str
    type: str = "auto"  # "image", "document", or "auto"


class ChatRequest(BaseModel):
    message: str
    model: str = ""
    chat_id: Optional[int] = None
    attachments: list[ChatAttachment] = []


class AgentCreateRequest(BaseModel):
    name: str
    system_prompt: str = ""
    model: str = ""
    description: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    top_p: float = 1.0


class AgentUpdateRequest(BaseModel):
    """Request body for PUT /api/agents/{name}"""
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    description: Optional[str] = None
    temperature: Optional[float] = None  # 0.0 - 2.0
    max_tokens: Optional[int] = None     # 1 - 128000
    top_p: Optional[float] = None        # 0.0 - 1.0


class AgentSwitchRequest(BaseModel):
    name: str


class ChatCreateRequest(BaseModel):
    agent_name: str = "Nova"
    title: str = "New Chat"


class ChatUpdateRequest(BaseModel):
    title: Optional[str] = None
    pinned: Optional[int] = None       # 0 or 1
    archived: Optional[int] = None     # 0 or 1
    folder: Optional[str] = None       # folder name or '' for uncategorized


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
# Chat message builder (handles attachments + vision)
# ---------------------------------------------------------------------------


async def _build_chat_messages(
    db, chat_id: int, body: ChatRequest, system_prompt: str, uid: str
) -> list[dict]:
    """Build the messages array for the LLM, handling attachments with vision format."""

    now = time.time()

    # Build a storage string that includes attachment references for history display
    storage_content = body.message
    attachment_meta: list[dict] = []
    if body.attachments:
        file_ids = [a.file_id for a in body.attachments]
        for fid in file_ids:
            rows = await db.execute_fetchall(
                "SELECT original_name, mime_type, is_image FROM uploads WHERE id=? AND user_id=?",
                (fid, uid),
            )
            if rows:
                attachment_meta.append({
                    "file_id": fid,
                    "name": rows[0]["original_name"],
                    "mime_type": rows[0]["mime_type"],
                    "is_image": bool(rows[0]["is_image"]),
                })
        if attachment_meta:
            meta_json = json.dumps(attachment_meta)
            storage_content = f"{body.message}\n\n<!-- attachments:{meta_json} -->"

    await db.execute(
        "INSERT INTO messages (chat_id, role, content, model, created_at) VALUES (?,?,?,?,?)",
        (chat_id, "user", storage_content, "", now),
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
    raw_history = [{"role": r["role"], "content": r["content"]} for r in history_rows]
    if len(raw_history) > 50:
        raw_history = raw_history[-50:]

    # Build messages list — convert the LAST user message if it has image attachments
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    for i, msg in enumerate(raw_history):
        is_last_user = (i == len(raw_history) - 1) and msg["role"] == "user"

        if is_last_user and body.attachments:
            # Build multimodal content for the current message
            image_attachments = []
            doc_texts = []

            for att in body.attachments:
                file_rows = await db.execute_fetchall(
                    "SELECT stored_path, mime_type, is_image FROM uploads WHERE id=? AND user_id=?",
                    (att.file_id, uid),
                )
                if not file_rows:
                    continue

                frow = file_rows[0]
                file_path = frow["stored_path"]
                mime = frow["mime_type"]
                is_img = bool(frow["is_image"])

                if is_img:
                    try:
                        b64 = _read_file_base64(file_path)
                        image_attachments.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"}
                        })
                    except Exception:
                        pass
                else:
                    try:
                        with open(file_path, "r", errors="replace") as f:
                            text = f.read(50000)
                        doc_texts.append(f"[Attached file: {Path(file_path).name}]\n{text}")
                    except Exception:
                        pass

            text_part = body.message
            if doc_texts:
                text_part += "\n\n" + "\n\n".join(doc_texts)

            if image_attachments:
                content_parts: list[dict] = [{"type": "text", "text": text_part}]
                content_parts.extend(image_attachments)
                messages.append({"role": "user", "content": content_parts})
            else:
                messages.append({"role": "user", "content": text_part})
        else:
            content = msg["content"]
            if "<!-- attachments:" in content:
                content = content.split("\n\n<!-- attachments:")[0]
            messages.append({"role": msg["role"], "content": content})

    return messages


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
    db = await get_db()
    await migrate_agents_table(db)
    await migrate_chats_table()
    # Create upload directory
    Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)


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
# Routes — Root / Health / Models
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


@app.get("/api/models")
async def list_models():
    """Return curated list of popular models organized by category."""
    return {
        "models": POPULAR_MODELS,
        "categories": MODEL_CATEGORIES,
        "default": DEFAULT_MODEL or "openrouter/auto",
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
# Uploads
# ---------------------------------------------------------------------------


@app.post("/api/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    """Upload a file (image or document). Returns metadata + optional thumbnail."""
    uid = request.state.user_id

    content = await file.read()
    size = len(content)

    error = _validate_upload(file.filename or "unknown", file.content_type, size)
    if error:
        raise HTTPException(status_code=400, detail=error)

    original_name = file.filename or "upload"
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "bin"
    mime_type = file.content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    is_image = mime_type in ALLOWED_IMAGE_TYPES

    file_id = uuid.uuid4().hex
    user_dir = Path(UPLOAD_DIR) / uid
    user_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{file_id}.{ext}"
    stored_path = user_dir / stored_filename

    with open(stored_path, "wb") as f:
        f.write(content)

    thumbnail = None
    if is_image:
        thumbnail = _make_thumbnail_base64(str(stored_path))

    db = await get_db()
    now = time.time()
    await db.execute(
        """INSERT INTO uploads (id, user_id, original_name, stored_path, mime_type, size, is_image, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (file_id, uid, original_name, str(stored_path), mime_type, size, int(is_image), now),
    )
    await db.commit()

    result: dict[str, Any] = {
        "id": file_id,
        "filename": original_name,
        "url": f"/api/uploads/{file_id}",
        "mime_type": mime_type,
        "size": size,
        "is_image": is_image,
    }
    if thumbnail:
        result["thumbnail"] = f"data:image/jpeg;base64,{thumbnail}"

    return result


@app.get("/api/uploads/{file_id}")
async def serve_upload(request: Request, file_id: str):
    """Serve an uploaded file by its ID."""
    uid = request.state.user_id
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT stored_path, mime_type, original_name FROM uploads WHERE id=? AND user_id=?",
        (file_id, uid),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="File not found")

    row = rows[0]
    file_path = row["stored_path"]

    if not Path(file_path).is_file():
        raise HTTPException(status_code=404, detail="File missing from disk")

    return FileResponse(
        path=file_path,
        media_type=row["mime_type"],
        filename=row["original_name"],
    )


# ---------------------------------------------------------------------------
# Chats — STATIC routes first (before parameterized /{chat_id})
# ---------------------------------------------------------------------------


@app.get("/api/chats")
async def list_chats(
    request: Request,
    pinned: Optional[int] = Query(None, description="Filter pinned chats (1=only pinned)"),
    archived: Optional[int] = Query(None, description="Filter archived chats (1=only archived, 0=exclude archived)"),
    folder: Optional[str] = Query(None, description="Filter by folder name"),
):
    """List user's chats with filtering. Pinned sort first, archived hidden by default."""
    uid = request.state.user_id
    db = await get_db()

    conditions = ["user_id = ?"]
    params: list = [uid]

    if pinned is not None:
        conditions.append("pinned = ?")
        params.append(pinned)

    if archived is not None:
        conditions.append("archived = ?")
        params.append(archived)
    else:
        conditions.append("archived = 0")

    if folder is not None:
        conditions.append("folder = ?")
        params.append(folder)

    where = " AND ".join(conditions)

    query = f"""
        SELECT id, title, agent_name, created_at, updated_at,
               COALESCE(pinned, 0) as pinned,
               COALESCE(archived, 0) as archived,
               COALESCE(folder, '') as folder
        FROM chats
        WHERE {where}
        ORDER BY COALESCE(pinned, 0) DESC, updated_at DESC
    """

    rows = await db.execute_fetchall(query, params)
    return {
        "chats": [
            {
                "id": r["id"],
                "title": r["title"],
                "agent_name": r["agent_name"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "pinned": r["pinned"],
                "archived": r["archived"],
                "folder": r["folder"],
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


@app.get("/api/chats/search")
async def search_chats(
    request: Request,
    q: str = Query("", description="Search query"),
):
    """Full-text search across chat titles and message content."""
    uid = request.state.user_id
    if not q.strip():
        return {"chats": [], "query": q}

    db = await get_db()
    search_term = f"%{q.strip()}%"

    query = """
        SELECT DISTINCT c.id, c.title, c.agent_name, c.created_at, c.updated_at,
               COALESCE(c.pinned, 0) as pinned,
               COALESCE(c.archived, 0) as archived,
               COALESCE(c.folder, '') as folder,
               COALESCE(m.content, '') as match_snippet,
               CASE
                   WHEN c.title LIKE ? THEN 'title'
                   ELSE 'message'
               END as match_type
        FROM chats c
        LEFT JOIN messages m ON m.chat_id = c.id AND m.content LIKE ?
        WHERE c.user_id = ?
          AND (c.title LIKE ? OR m.content LIKE ?)
        GROUP BY c.id
        ORDER BY c.updated_at DESC
        LIMIT 50
    """

    rows = await db.execute_fetchall(
        query, (search_term, search_term, uid, search_term, search_term)
    )

    results = []
    for r in rows:
        snippet = r["match_snippet"] or ""
        if snippet and r["match_type"] == "message":
            lower_snippet = snippet.lower()
            idx = lower_snippet.find(q.strip().lower())
            if idx >= 0:
                start = max(0, idx - 40)
                end = min(len(snippet), idx + len(q.strip()) + 40)
                snippet = ("…" if start > 0 else "") + snippet[start:end] + ("…" if end < len(snippet) else "")
            else:
                snippet = snippet[:80] + ("…" if len(snippet) > 80 else "")
        else:
            snippet = ""

        results.append({
            "id": r["id"],
            "title": r["title"],
            "agent_name": r["agent_name"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "pinned": r["pinned"],
            "archived": r["archived"],
            "folder": r["folder"],
            "match_snippet": snippet,
            "match_type": r["match_type"],
        })

    return {"chats": results, "query": q}


@app.get("/api/chats/folders")
async def list_folders(request: Request):
    """List all unique folder names for the current user."""
    uid = request.state.user_id
    db = await get_db()

    rows = await db.execute_fetchall(
        """SELECT DISTINCT COALESCE(folder, '') as folder
           FROM chats
           WHERE user_id = ? AND COALESCE(folder, '') != ''
           ORDER BY folder""",
        (uid,),
    )

    return {"folders": [r["folder"] for r in rows]}


@app.get("/api/chats/archived/count")
async def archived_count(request: Request):
    """Get count of archived chats for the current user."""
    uid = request.state.user_id
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT COUNT(*) as cnt FROM chats WHERE user_id = ? AND COALESCE(archived, 0) = 1",
        (uid,),
    )

    return {"count": rows[0]["cnt"] if rows else 0}


# ---------------------------------------------------------------------------
# Chats — PARAMETERIZED routes (after static routes)
# ---------------------------------------------------------------------------


@app.delete("/api/chats/{chat_id}")
async def delete_chat(request: Request, chat_id: int):
    """Delete a chat and its messages."""
    uid = request.state.user_id
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id FROM chats WHERE id=? AND user_id=?", (chat_id, uid)
    )
    if not rows:
        return JSONResponse({"error": "Chat not found"}, 404)
    await db.execute("DELETE FROM chats WHERE id=?", (chat_id,))
    await db.commit()
    return {"deleted": True, "chat_id": chat_id}


@app.put("/api/chats/{chat_id}")
async def update_chat(request: Request, chat_id: int, body: ChatUpdateRequest):
    """Update chat title, pinned status, folder, or archived flag."""
    uid = request.state.user_id
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT id FROM chats WHERE id=? AND user_id=?", (chat_id, uid)
    )
    if not rows:
        return JSONResponse({"error": "Chat not found"}, 404)

    updates = []
    params: list = []

    if body.title is not None:
        updates.append("title = ?")
        params.append(body.title.strip())
    if body.pinned is not None:
        updates.append("pinned = ?")
        params.append(1 if body.pinned else 0)
    if body.archived is not None:
        updates.append("archived = ?")
        params.append(1 if body.archived else 0)
    if body.folder is not None:
        updates.append("folder = ?")
        params.append(body.folder.strip())

    if not updates:
        return JSONResponse({"error": "No fields to update"}, 400)

    now = time.time()
    updates.append("updated_at = ?")
    params.append(now)

    params.append(chat_id)
    set_clause = ", ".join(updates)
    await db.execute(f"UPDATE chats SET {set_clause} WHERE id = ?", params)
    await db.commit()

    row = (await db.execute_fetchall(
        """SELECT id, title, agent_name, created_at, updated_at,
                  COALESCE(pinned, 0) as pinned,
                  COALESCE(archived, 0) as archived,
                  COALESCE(folder, '') as folder
           FROM chats WHERE id = ?""",
        (chat_id,),
    ))[0]

    return {
        "id": row["id"],
        "title": row["title"],
        "agent_name": row["agent_name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "pinned": row["pinned"],
        "archived": row["archived"],
        "folder": row["folder"],
    }


@app.get("/api/chats/{chat_id}/messages")
async def get_chat_messages(request: Request, chat_id: int):
    """Get all messages for a chat."""
    uid = request.state.user_id
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id FROM chats WHERE id=? AND user_id=?", (chat_id, uid)
    )
    if not rows:
        return JSONResponse({"error": "Chat not found"}, 404)

    msgs = await db.execute_fetchall(
        "SELECT id, role, content, model, created_at FROM messages WHERE chat_id=? ORDER BY created_at ASC",
        (chat_id,),
    )
    return {
        "messages": [
            {
                "id": m["id"],
                "role": m["role"],
                "content": m["content"],
                "model": m["model"] or "",
                "created_at": m["created_at"],
            }
            for m in msgs
        ]
    }


@app.get("/api/chats/{chat_id}/export")
async def export_chat(
    request: Request,
    chat_id: int,
    format: str = Query("markdown", description="Export format (markdown)"),
):
    """Export a chat as a downloadable markdown file."""
    uid = request.state.user_id
    db = await get_db()

    chat_rows = await db.execute_fetchall(
        """SELECT id, title, agent_name, created_at, updated_at
           FROM chats WHERE id=? AND user_id=?""",
        (chat_id, uid),
    )
    if not chat_rows:
        return JSONResponse({"error": "Chat not found"}, 404)

    chat = chat_rows[0]
    title = chat["title"] or "Untitled Chat"
    agent_name = chat["agent_name"] or "Nova"

    msgs = await db.execute_fetchall(
        "SELECT role, content, created_at FROM messages WHERE chat_id=? ORDER BY created_at ASC",
        (chat_id,),
    )

    lines = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"**Agent:** {agent_name}")

    created_dt = datetime.fromtimestamp(chat["created_at"], tz=timezone.utc)
    lines.append(f"**Created:** {created_dt.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Messages:** {len(list(msgs))}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for msg in msgs:
        role_label = "You" if msg["role"] == "user" else agent_name
        msg_dt = datetime.fromtimestamp(msg["created_at"], tz=timezone.utc)
        timestamp = msg_dt.strftime("%Y-%m-%d %H:%M")
        lines.append(f"### {role_label} — {timestamp}")
        lines.append("")
        lines.append(msg["content"])
        lines.append("")
        lines.append("---")
        lines.append("")

    md_content = "\n".join(lines)

    safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '-')[:50]
    filename = f"nova-chat-{safe_title}-{chat_id}.md"

    return PlainTextResponse(
        content=md_content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ---------------------------------------------------------------------------
# Agents — STATIC routes first
# ---------------------------------------------------------------------------


@app.get("/api/agents/templates")
async def get_agent_templates(request: Request):
    """Return the list of pre-built agent templates."""
    return {"templates": AGENT_TEMPLATES}


@app.get("/api/agents")
async def list_agents(request: Request):
    """List user's agents from DB (enhanced with new fields)."""
    uid = request.state.user_id
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id, name, system_prompt, model, description, temperature, max_tokens, top_p, created_at "
        "FROM agents WHERE user_id=? ORDER BY created_at ASC",
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
                "description": r["description"] or "",
                "temperature": r["temperature"] if r["temperature"] is not None else 0.7,
                "max_tokens": r["max_tokens"] if r["max_tokens"] is not None else 4096,
                "top_p": r["top_p"] if r["top_p"] is not None else 1.0,
                "created_at": r["created_at"],
            }
            for r in rows
        ],
        "active": active,
    }


@app.post("/api/agents")
async def create_agent(request: Request, body: AgentCreateRequest):
    """Create a new agent in DB (enhanced with new fields)."""
    uid = request.state.user_id
    name = body.name.strip()
    if not name:
        return JSONResponse({"error": "Agent name is required"}, 400)

    db = await get_db()
    now = time.time()
    temp = max(0.0, min(2.0, body.temperature))
    max_tok = max(1, min(128000, body.max_tokens))
    top_p_val = max(0.0, min(1.0, body.top_p))

    try:
        cursor = await db.execute(
            "INSERT INTO agents (user_id, name, system_prompt, model, description, temperature, max_tokens, top_p, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                uid,
                name,
                body.system_prompt or f"You are {name}, a helpful assistant.",
                body.model,
                body.description,
                temp,
                max_tok,
                top_p_val,
                now,
            ),
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
        "description": body.description,
        "temperature": temp,
        "max_tokens": max_tok,
        "top_p": top_p_val,
        "created_at": now,
    }


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
# Agents — PARAMETERIZED routes
# ---------------------------------------------------------------------------


@app.put("/api/agents/{name}")
async def update_agent(request: Request, name: str, body: AgentUpdateRequest):
    """Update an existing agent's configuration. Supports partial updates."""
    uid = request.state.user_id
    db = await get_db()

    rows = await db.execute_fetchall(
        "SELECT id, name, system_prompt, model, description, temperature, max_tokens, top_p "
        "FROM agents WHERE user_id=? AND name=?",
        (uid, name),
    )
    if not rows:
        return JSONResponse({"error": f"Agent '{name}' not found"}, 404)

    updates: dict[str, Any] = {}
    if body.system_prompt is not None:
        updates["system_prompt"] = body.system_prompt
    if body.model is not None:
        updates["model"] = body.model
    if body.description is not None:
        updates["description"] = body.description
    if body.temperature is not None:
        updates["temperature"] = max(0.0, min(2.0, body.temperature))
    if body.max_tokens is not None:
        updates["max_tokens"] = max(1, min(128000, body.max_tokens))
    if body.top_p is not None:
        updates["top_p"] = max(0.0, min(1.0, body.top_p))
    if body.name is not None and body.name.strip() and body.name.strip() != name:
        new_name = body.name.strip()
        existing = await db.execute_fetchall(
            "SELECT id FROM agents WHERE user_id=? AND name=?",
            (uid, new_name),
        )
        if existing:
            return JSONResponse({"error": f"Agent '{new_name}' already exists"}, 409)
        updates["name"] = new_name
        if _active_agents.get(uid) == name:
            _active_agents[uid] = new_name
        await db.execute(
            "UPDATE chats SET agent_name=? WHERE user_id=? AND agent_name=?",
            (new_name, uid, name),
        )

    if not updates:
        return JSONResponse({"error": "No fields to update"}, 400)

    set_clauses = ", ".join(f"{k}=?" for k in updates.keys())
    values = list(updates.values()) + [uid, name]
    await db.execute(
        f"UPDATE agents SET {set_clauses} WHERE user_id=? AND name=?",
        values,
    )
    await db.commit()

    final_name = updates.get("name", name)
    updated_rows = await db.execute_fetchall(
        "SELECT id, name, system_prompt, model, description, temperature, max_tokens, top_p, created_at "
        "FROM agents WHERE user_id=? AND name=?",
        (uid, final_name),
    )
    if updated_rows:
        r = updated_rows[0]
        return {
            "id": r["id"],
            "name": r["name"],
            "system_prompt": r["system_prompt"],
            "model": r["model"],
            "description": r["description"] or "",
            "temperature": r["temperature"] if r["temperature"] is not None else 0.7,
            "max_tokens": r["max_tokens"] if r["max_tokens"] is not None else 4096,
            "top_p": r["top_p"] if r["top_p"] is not None else 1.0,
            "created_at": r["created_at"],
        }
    return {"updated": True, "name": final_name}


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


# ---------------------------------------------------------------------------
# Chat (streaming SSE) — persistent, with attachments + agent params
# ---------------------------------------------------------------------------


@app.post("/api/chat")
async def chat(request: Request, body: ChatRequest):
    """Stream a chat response via SSE. Persists messages to DB. Supports file attachments."""
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
        title = body.message[:50].strip() or "New Chat"
        cursor = await db.execute(
            "INSERT INTO chats (user_id, agent_name, title, created_at, updated_at) VALUES (?,?,?,?,?)",
            (uid, active_agent, title, now, now),
        )
        await db.commit()
        chat_id = cursor.lastrowid
    else:
        rows = await db.execute_fetchall(
            "SELECT id, agent_name FROM chats WHERE id=? AND user_id=?", (chat_id, uid)
        )
        if not rows:
            return JSONResponse({"error": "Chat not found"}, 404)
        active_agent = rows[0]["agent_name"]

    # Get agent config (including temperature, max_tokens, top_p)
    agent_rows = await db.execute_fetchall(
        "SELECT system_prompt, model, temperature, max_tokens, top_p FROM agents WHERE user_id=? AND name=?",
        (uid, active_agent),
    )
    if agent_rows:
        agent_system = agent_rows[0]["system_prompt"] or NOVA_DEFAULT_SYSTEM
        agent_model = agent_rows[0]["model"] or ""
        agent_temperature = agent_rows[0]["temperature"] if agent_rows[0]["temperature"] is not None else 0.7
        agent_max_tokens = agent_rows[0]["max_tokens"] if agent_rows[0]["max_tokens"] is not None else 4096
        agent_top_p = agent_rows[0]["top_p"] if agent_rows[0]["top_p"] is not None else 1.0
    else:
        agent_system = NOVA_DEFAULT_SYSTEM
        agent_model = ""
        agent_temperature = 0.7
        agent_max_tokens = 4096
        agent_top_p = 1.0

    model = _resolve_model(provider, body.model or agent_model)
    system_prompt = agent_system

    # Build messages with attachment support
    messages = await _build_chat_messages(db, chat_id, body, system_prompt, uid)

    client = _build_client(provider)
    if not client:
        return JSONResponse({"error": "Failed to build LLM client"}, 500)

    # Capture for SSE closure
    _chat_id = chat_id
    _model = model
    _temperature = agent_temperature
    _max_tokens = agent_max_tokens
    _top_p = agent_top_p

    async def event_stream() -> AsyncGenerator[dict[str, str], None]:
        full_response = ""
        try:
            stream = await client.chat.completions.create(
                model=_model,
                messages=messages,
                stream=True,
                temperature=_temperature,
                max_tokens=_max_tokens,
                top_p=_top_p,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    full_response += delta.content
                    yield {"event": "token", "data": json.dumps({"token": delta.content})}

            # Save assistant response to DB with model info
            save_time = time.time()
            save_db = await get_db()
            await save_db.execute(
                "INSERT INTO messages (chat_id, role, content, model, created_at) VALUES (?,?,?,?,?)",
                (_chat_id, "assistant", full_response, _model, save_time),
            )
            await save_db.execute(
                "UPDATE chats SET updated_at=? WHERE id=?", (save_time, _chat_id)
            )
            await save_db.commit()

            yield {
                "event": "done",
                "data": json.dumps({
                    "model": _model,
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
        rows = await db.execute_fetchall(
            "SELECT id FROM chats WHERE id=? AND user_id=?", (chat_id, uid)
        )
        if not rows:
            return JSONResponse({"error": "Chat not found"}, 404)
        await db.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
        await db.commit()
        return {"cleared": True, "chat_id": chat_id}
    else:
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
