"""
Nova AI — Multi-Provider LLM System
Supports free-tier providers + BYOK (Bring Your Own Key)

Free (server-side keys):
  - Google Gemini (gemini-2.0-flash)
  - Groq (llama-3.1-70b-versatile)

BYOK:
  - OpenAI (gpt-4o, gpt-4o-mini)
  - Anthropic (claude-sonnet-4-20250514)
  - OpenRouter (any model)
"""

import os
import json
import logging
import httpx
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

# ── Provider Registry ─────────────────────────────────────────────────

MODELS = {
    # Free tier models (server-side keys)
    "gemini-2.0-flash": {
        "provider": "gemini",
        "name": "Gemini 2.0 Flash",
        "model_id": "gemini-2.0-flash",
        "free": True,
        "tier": "free",
    },
    "gemini-2.5-flash": {
        "provider": "gemini",
        "name": "Gemini 2.5 Flash",
        "model_id": "gemini-2.5-flash-preview-05-20",
        "free": True,
        "tier": "free",
    },
    "llama-3.1-70b": {
        "provider": "groq",
        "name": "Llama 3.1 70B",
        "model_id": "llama-3.1-70b-versatile",
        "free": True,
        "tier": "free",
    },
    "llama-3.3-70b": {
        "provider": "groq",
        "name": "Llama 3.3 70B",
        "model_id": "llama-3.3-70b-versatile",
        "free": True,
        "tier": "free",
    },
    # BYOK models
    "gpt-4o": {
        "provider": "openai",
        "name": "GPT-4o",
        "model_id": "gpt-4o",
        "free": False,
        "tier": "byok",
    },
    "gpt-4o-mini": {
        "provider": "openai",
        "name": "GPT-4o Mini",
        "model_id": "gpt-4o-mini",
        "free": False,
        "tier": "byok",
    },
    "claude-sonnet": {
        "provider": "anthropic",
        "name": "Claude Sonnet 4",
        "model_id": "claude-sonnet-4-20250514",
        "free": False,
        "tier": "byok",
    },
    "openrouter-auto": {
        "provider": "openrouter",
        "name": "OpenRouter Auto",
        "model_id": "openrouter/auto",
        "free": False,
        "tier": "byok",
    },
}

def get_available_models() -> list[dict]:
    """Return list of models with availability status."""
    result = []
    for key, info in MODELS.items():
        available = True
        if info["free"]:
            # Free models need server-side key
            if info["provider"] == "gemini":
                available = bool(os.environ.get("GEMINI_API_KEY"))
            elif info["provider"] == "groq":
                available = bool(os.environ.get("GROQ_API_KEY"))
        else:
            available = False  # BYOK always shows but needs user key

        result.append({
            "id": key,
            "name": info["name"],
            "provider": info["provider"],
            "free": info["free"],
            "tier": info["tier"],
            "available": available,
        })
    return result


# ── Provider Implementations ──────────────────────────────────────────

async def stream_gemini(
    messages: list[dict],
    model_id: str,
    api_key: str,
) -> AsyncIterator[str]:
    """Stream from Google Gemini API."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:streamGenerateContent"

    # Convert messages to Gemini format
    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 4096,
        },
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            url,
            params={"key": api_key, "alt": "sse"},
            json=payload,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise Exception(f"Gemini API error {resp.status_code}: {body.decode()[:200]}")

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for part in parts:
                            text = part.get("text", "")
                            if text:
                                yield text
                except json.JSONDecodeError:
                    continue


async def stream_groq(
    messages: list[dict],
    model_id: str,
    api_key: str,
) -> AsyncIterator[str]:
    """Stream from Groq API (OpenAI-compatible)."""
    url = "https://api.groq.com/openai/v1/chat/completions"

    payload = {
        "model": model_id,
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
        "max_tokens": 4096,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise Exception(f"Groq API error {resp.status_code}: {body.decode()[:200]}")

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content", "")
                    if text:
                        yield text
                except json.JSONDecodeError:
                    continue


async def stream_openai_compat(
    messages: list[dict],
    model_id: str,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
) -> AsyncIterator[str]:
    """Stream from any OpenAI-compatible API (OpenAI, OpenRouter)."""
    url = f"{base_url}/chat/completions"

    payload = {
        "model": model_id,
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
        "max_tokens": 4096,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise Exception(f"API error {resp.status_code}: {body.decode()[:200]}")

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content", "")
                    if text:
                        yield text
                except json.JSONDecodeError:
                    continue


async def stream_anthropic(
    messages: list[dict],
    model_id: str,
    api_key: str,
) -> AsyncIterator[str]:
    """Stream from Anthropic API."""
    url = "https://api.anthropic.com/v1/messages"

    # Separate system message
    system = ""
    chat_msgs = []
    for msg in messages:
        if msg["role"] == "system":
            system = msg["content"]
        else:
            chat_msgs.append({"role": msg["role"], "content": msg["content"]})

    payload = {
        "model": model_id,
        "messages": chat_msgs,
        "max_tokens": 4096,
        "stream": True,
    }
    if system:
        payload["system"] = system

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise Exception(f"Anthropic API error {resp.status_code}: {body.decode()[:200]}")

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                try:
                    data = json.loads(data_str)
                    if data.get("type") == "content_block_delta":
                        text = data.get("delta", {}).get("text", "")
                        if text:
                            yield text
                except json.JSONDecodeError:
                    continue


# ── Main Chat Function ────────────────────────────────────────────────

async def chat_stream(
    model_key: str,
    messages: list[dict],
    user_api_key: Optional[str] = None,
) -> AsyncIterator[str]:
    """Route to the correct provider and stream tokens."""

    if model_key not in MODELS:
        raise ValueError(f"Unknown model: {model_key}")

    info = MODELS[model_key]
    provider = info["provider"]
    model_id = info["model_id"]

    # Determine API key
    if info["free"]:
        # Use server-side key
        if provider == "gemini":
            api_key = os.environ.get("GEMINI_API_KEY", "")
        elif provider == "groq":
            api_key = os.environ.get("GROQ_API_KEY", "")
        else:
            api_key = ""

        if not api_key:
            raise ValueError(f"Server-side key not configured for {provider}")
    else:
        # BYOK — user must provide key
        if not user_api_key:
            raise ValueError(f"{info['name']} requires an API key. Add yours in Settings.")
        api_key = user_api_key

    # Add system message
    system_msg = {
        "role": "system",
        "content": (
            "You are Nova, a premium AI assistant. You are helpful, intelligent, "
            "and concise. You speak in a calm, knowledgeable tone. "
            "Format responses with markdown when helpful."
        ),
    }
    full_messages = [system_msg] + messages

    # Route to provider
    if provider == "gemini":
        async for token in stream_gemini(full_messages, model_id, api_key):
            yield token
    elif provider == "groq":
        async for token in stream_groq(full_messages, model_id, api_key):
            yield token
    elif provider == "openai":
        async for token in stream_openai_compat(full_messages, model_id, api_key):
            yield token
    elif provider == "anthropic":
        async for token in stream_anthropic(full_messages, model_id, api_key):
            yield token
    elif provider == "openrouter":
        async for token in stream_openai_compat(
            full_messages, model_id, api_key,
            base_url="https://openrouter.ai/api/v1",
        ):
            yield token
    else:
        raise ValueError(f"Unknown provider: {provider}")
