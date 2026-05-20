"""
Multi-Provider LLM Client — Routes through prioritized providers with automatic fallback.

Supports OpenRouter, OpenAI, and Ollama with connectivity heartbeat,
automatic failover, and runtime model switching.  Replaces HybridLLMClient
as the main LLM interface.
"""

from __future__ import annotations

import json as _json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Generator, Optional

import requests

import config

logger = logging.getLogger(__name__)

# Try importing openai
try:
    import openai as _openai_pkg

    _OPENAI_AVAILABLE = True
except ImportError:
    _openai_pkg = None  # type: ignore[assignment]
    _OPENAI_AVAILABLE = False
    logger.info("openai package not installed — cloud providers unavailable")


# ── Provider dataclass ────────────────────────────────────────────────


@dataclass
class LLMProvider:
    """Single provider configuration and runtime state."""

    name: str  # "ollama", "openai", "openrouter"
    api_key: str  # API key (empty for ollama)
    base_url: str  # API base URL
    model: str  # Model identifier
    priority: int  # Lower = preferred (0 = highest)
    is_local: bool = False  # True for ollama
    timeout: int = 30  # Request timeout seconds
    enabled: bool = True  # Can be disabled at runtime

    # Runtime state (not config)
    _healthy: bool = field(default=True, init=False, repr=False)
    _permanently_disabled: bool = field(default=False, init=False, repr=False)
    _last_error: Optional[str] = field(default=None, init=False, repr=False)

    @property
    def available(self) -> bool:
        """True if this provider can accept requests right now."""
        return self.enabled and self._healthy and not self._permanently_disabled

    def status_dict(self) -> dict:
        """Serialisable status for the web hub."""
        return {
            "name": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "priority": self.priority,
            "is_local": self.is_local,
            "enabled": self.enabled,
            "healthy": self._healthy,
            "permanently_disabled": self._permanently_disabled,
            "last_error": self._last_error,
            "available": self.available,
        }


# ── Multi-Provider LLM ───────────────────────────────────────────────


class MultiProviderLLM:
    """Routes chat requests through prioritised providers with automatic fallback.

    Parameters
    ----------
    system_prompt:
        System message prepended to every request.
    max_history:
        Maximum user/assistant exchange *pairs* to retain.
    on_provider_change:
        Callback ``fn(provider_name: str)`` when active provider changes.
    heartbeat_interval:
        Seconds between connectivity heartbeat checks.
    """

    def __init__(
        self,
        system_prompt: str = config.LLM_SYSTEM_PROMPT,
        max_history: int = config.LLM_MAX_HISTORY,
        on_provider_change: Optional[Callable[[str], None]] = None,
        heartbeat_interval: float = config.PROVIDER_HEARTBEAT_INTERVAL,
    ) -> None:
        self.system_prompt = system_prompt
        self.max_history = max_history
        self._on_provider_change = on_provider_change
        self._heartbeat_interval = heartbeat_interval

        self._history: list[dict[str, str]] = []
        self._lock = threading.Lock()
        self._active_provider: Optional[str] = None

        # HTTP session for Ollama (keep-alive pooling)
        self._http_session = requests.Session()
        self._http_session.headers.update({"Connection": "keep-alive"})

        # Build provider list from config
        self._providers: list[LLMProvider] = self._build_providers()

        # OpenAI-compatible client cache: provider-name → openai.OpenAI
        self._openai_clients: dict[str, _openai_pkg.OpenAI] = {}  # type: ignore[union-attr]
        if _OPENAI_AVAILABLE:
            self._init_openai_clients()

        # Pick initial active provider
        self._resolve_active()

        # Heartbeat thread
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="provider-heartbeat",
            daemon=True,
        )

    # ------------------------------------------------------------------
    # Provider setup
    # ------------------------------------------------------------------

    @staticmethod
    def _build_providers() -> list[LLMProvider]:
        """Build the provider list from config, ordered by priority string."""
        priority_order = [
            p.strip().lower()
            for p in config.LLM_PROVIDER_PRIORITY.split(",")
            if p.strip()
        ]

        available: dict[str, LLMProvider] = {}

        # OpenRouter
        if config.OPENROUTER_API_KEY:
            available["openrouter"] = LLMProvider(
                name="openrouter",
                api_key=config.OPENROUTER_API_KEY,
                base_url=config.OPENROUTER_BASE_URL,
                model=config.OPENROUTER_MODEL,
                priority=0,  # placeholder, set below
                is_local=False,
                timeout=60,
            )

        # OpenAI (direct)
        openai_key = _get_openai_key()
        if openai_key:
            available["openai"] = LLMProvider(
                name="openai",
                api_key=openai_key,
                base_url="https://api.openai.com/v1",
                model=config.OPENAI_MODEL,
                priority=0,
                is_local=False,
                timeout=30,
            )

        # Ollama (always available)
        available["ollama"] = LLMProvider(
            name="ollama",
            api_key="",
            base_url=config.OLLAMA_BASE_URL,
            model=config.OLLAMA_MODEL,
            priority=0,
            is_local=True,
            timeout=config.OLLAMA_TIMEOUT,
        )

        # Assign priorities based on config order
        providers: list[LLMProvider] = []
        for idx, name in enumerate(priority_order):
            if name in available:
                p = available.pop(name)
                p.priority = idx
                providers.append(p)

        # Append any remaining providers not in priority string
        for remaining in available.values():
            remaining.priority = len(providers)
            providers.append(remaining)

        providers.sort(key=lambda p: p.priority)

        for p in providers:
            logger.info(
                "Provider [%d] %s — model=%s local=%s enabled=%s",
                p.priority,
                p.name,
                p.model,
                p.is_local,
                p.enabled,
            )

        return providers

    def _init_openai_clients(self) -> None:
        """Create ``openai.OpenAI`` clients for cloud providers."""
        for p in self._providers:
            if p.is_local or not p.api_key:
                continue
            try:
                client = _openai_pkg.OpenAI(  # type: ignore[union-attr]
                    api_key=p.api_key,
                    base_url=p.base_url,
                    timeout=float(p.timeout),
                )
                self._openai_clients[p.name] = client
                logger.info("OpenAI-compat client created for %s", p.name)
            except Exception as exc:
                logger.warning("Failed to create client for %s: %s", p.name, exc)
                p.enabled = False

    # ------------------------------------------------------------------
    # Public API — matches OllamaClient / HybridLLMClient interface
    # ------------------------------------------------------------------

    @property
    def history(self) -> list[dict[str, str]]:
        """Return a copy of the current conversation history."""
        return list(self._history)

    def inject_context(self, role: str, content: str) -> None:
        """Inject a message into history without triggering an LLM call."""
        self._history.append({"role": role, "content": content})
        self._trim_history()
        logger.debug("Injected context [%s]: %s", role, content[:80])

    def clear_history(self) -> None:
        """Erase the conversation history."""
        self._history.clear()
        logger.info("Conversation history cleared")

    def chat(self, user_message: str) -> Optional[str]:
        """Send a user message, return the assistant's reply.

        Tries providers in priority order with automatic fallback.
        """
        self._history.append({"role": "user", "content": user_message})
        messages = self._build_messages()

        for provider in self._get_ordered_providers():
            try:
                reply = self._chat_single(provider, messages)
                if reply is not None:
                    self._history.append({"role": "assistant", "content": reply})
                    self._trim_history()
                    self._set_active(provider.name)
                    logger.info("[%s] Reply: %s", provider.name.upper(), reply[:120])
                    return reply
            except _PermanentError:
                continue
            except _TemporaryError:
                continue

        # All providers failed — remove unanswered user message
        logger.error("All providers failed for chat()")
        if self._history and self._history[-1]["role"] == "user":
            self._history.pop()
        return None

    def chat_stream(self, user_message: str) -> Generator[str, None, None]:
        """Stream tokens from the best available provider with fallback."""
        self._history.append({"role": "user", "content": user_message})
        messages = self._build_messages()

        for provider in self._get_ordered_providers():
            try:
                parts: list[str] = []
                streamed = False
                for token in self._stream_single(provider, messages):
                    streamed = True
                    parts.append(token)
                    yield token

                if streamed:
                    full_reply = "".join(parts)
                    if full_reply:
                        self._history.append(
                            {"role": "assistant", "content": full_reply}
                        )
                    else:
                        if self._history and self._history[-1]["role"] == "user":
                            self._history.pop()
                    self._trim_history()
                    self._set_active(provider.name)
                    logger.info(
                        "[%s] Stream reply: %s",
                        provider.name.upper(),
                        full_reply[:120] if full_reply else "(empty)",
                    )
                    return
                # No tokens yielded — treat as failure
                raise _TemporaryError(f"{provider.name}: empty stream")

            except _PermanentError:
                continue
            except _TemporaryError:
                continue

        # All providers failed
        logger.error("All providers failed for chat_stream()")
        if self._history and self._history[-1]["role"] == "user":
            self._history.pop()

    # ------------------------------------------------------------------
    # Provider status / runtime control
    # ------------------------------------------------------------------

    def get_active_provider(self) -> str:
        """Return name of the currently active provider."""
        return self._active_provider or "none"

    def get_providers_status(self) -> list[dict]:
        """Return status of all providers (for UI/web hub)."""
        return [p.status_dict() for p in self._providers]

    def set_model(self, provider_name: str, model: str) -> None:
        """Change model for a provider at runtime."""
        for p in self._providers:
            if p.name == provider_name:
                old = p.model
                p.model = model
                logger.info(
                    "Model changed for %s: %s → %s", provider_name, old, model
                )
                return
        logger.warning("Provider %s not found for set_model()", provider_name)

    # Backwards-compat property so pipeline._on_llm_mode_change works
    @property
    def mode(self) -> str:
        """Compat: return 'cloud' or 'local' depending on active provider."""
        active = self._active_provider
        if active is None:
            return "local"
        for p in self._providers:
            if p.name == active:
                return "local" if p.is_local else "cloud"
        return "local"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the heartbeat checker."""
        self._heartbeat_stop.clear()
        if not self._heartbeat_thread.is_alive():
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name="provider-heartbeat",
                daemon=True,
            )
            self._heartbeat_thread.start()
        # Initial check
        self._run_heartbeat()
        logger.info("MultiProviderLLM started (active: %s)", self._active_provider)

    def stop(self) -> None:
        """Stop the heartbeat checker and clean up."""
        self._heartbeat_stop.set()
        logger.info("MultiProviderLLM stopped")

    # ------------------------------------------------------------------
    # Per-provider chat implementations
    # ------------------------------------------------------------------

    def _chat_single(
        self, provider: LLMProvider, messages: list[dict]
    ) -> Optional[str]:
        """Blocking chat on a single provider. Raises on failure."""
        if provider.is_local:
            return self._chat_ollama(provider, messages)
        return self._chat_openai_compat(provider, messages)

    def _stream_single(
        self, provider: LLMProvider, messages: list[dict]
    ) -> Generator[str, None, None]:
        """Stream tokens from a single provider. Raises on failure."""
        if provider.is_local:
            yield from self._stream_ollama(provider, messages)
        else:
            yield from self._stream_openai_compat(provider, messages)

    # -- OpenAI-compatible (OpenRouter + OpenAI direct) --

    def _chat_openai_compat(
        self, provider: LLMProvider, messages: list[dict]
    ) -> Optional[str]:
        """Chat via OpenAI-compatible API."""
        client = self._openai_clients.get(provider.name)
        if client is None:
            raise _TemporaryError(f"No client for {provider.name}")

        try:
            response = client.chat.completions.create(
                model=provider.model,
                messages=messages,
                timeout=float(provider.timeout),
            )
            reply = response.choices[0].message.content
            provider._healthy = True
            provider._last_error = None
            return reply

        except _openai_pkg.AuthenticationError as exc:  # type: ignore[union-attr]
            logger.error("[%s] Auth error — disabling permanently: %s", provider.name, exc)
            provider._permanently_disabled = True
            provider._last_error = f"auth: {exc}"
            raise _PermanentError(str(exc)) from exc

        except _openai_pkg.RateLimitError as exc:  # type: ignore[union-attr]
            logger.warning("[%s] Rate limited — trying next: %s", provider.name, exc)
            provider._last_error = f"rate_limit: {exc}"
            # Don't mark unhealthy on rate limit
            raise _TemporaryError(str(exc)) from exc

        except (_openai_pkg.APIConnectionError, _openai_pkg.APITimeoutError) as exc:  # type: ignore[union-attr]
            logger.warning("[%s] Connection/timeout — marking down: %s", provider.name, exc)
            provider._healthy = False
            provider._last_error = f"connection: {exc}"
            raise _TemporaryError(str(exc)) from exc

        except _openai_pkg.APIStatusError as exc:  # type: ignore[union-attr]
            status = getattr(exc, "status_code", 0)
            if status in (401, 403):
                logger.error("[%s] Auth error (%d) — disabling: %s", provider.name, status, exc)
                provider._permanently_disabled = True
                provider._last_error = f"auth_{status}: {exc}"
                raise _PermanentError(str(exc)) from exc
            if status >= 500:
                logger.warning("[%s] Server error (%d) — marking down: %s", provider.name, status, exc)
                provider._healthy = False
                provider._last_error = f"server_{status}: {exc}"
                raise _TemporaryError(str(exc)) from exc
            # Other status errors
            logger.warning("[%s] API error (%d): %s", provider.name, status, exc)
            provider._last_error = f"api_{status}: {exc}"
            raise _TemporaryError(str(exc)) from exc

        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Unexpected error: %s", provider.name, exc)
            provider._healthy = False
            provider._last_error = str(exc)
            raise _TemporaryError(str(exc)) from exc

    def _stream_openai_compat(
        self, provider: LLMProvider, messages: list[dict]
    ) -> Generator[str, None, None]:
        """Stream tokens from OpenAI-compatible API."""
        client = self._openai_clients.get(provider.name)
        if client is None:
            raise _TemporaryError(f"No client for {provider.name}")

        try:
            response = client.chat.completions.create(
                model=provider.model,
                messages=messages,
                timeout=float(provider.timeout),
                stream=True,
            )
            for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield delta.content

            provider._healthy = True
            provider._last_error = None

        except _openai_pkg.AuthenticationError as exc:  # type: ignore[union-attr]
            logger.error("[%s] Auth error — disabling permanently: %s", provider.name, exc)
            provider._permanently_disabled = True
            provider._last_error = f"auth: {exc}"
            raise _PermanentError(str(exc)) from exc

        except _openai_pkg.RateLimitError as exc:  # type: ignore[union-attr]
            logger.warning("[%s] Rate limited — trying next: %s", provider.name, exc)
            provider._last_error = f"rate_limit: {exc}"
            raise _TemporaryError(str(exc)) from exc

        except (_openai_pkg.APIConnectionError, _openai_pkg.APITimeoutError) as exc:  # type: ignore[union-attr]
            logger.warning("[%s] Connection/timeout — marking down: %s", provider.name, exc)
            provider._healthy = False
            provider._last_error = f"connection: {exc}"
            raise _TemporaryError(str(exc)) from exc

        except _openai_pkg.APIStatusError as exc:  # type: ignore[union-attr]
            status = getattr(exc, "status_code", 0)
            if status in (401, 403):
                provider._permanently_disabled = True
                provider._last_error = f"auth_{status}: {exc}"
                raise _PermanentError(str(exc)) from exc
            provider._healthy = status < 500
            provider._last_error = f"api_{status}: {exc}"
            raise _TemporaryError(str(exc)) from exc

        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Stream error: %s", provider.name, exc)
            provider._healthy = False
            provider._last_error = str(exc)
            raise _TemporaryError(str(exc)) from exc

    # -- Ollama (native HTTP API) --

    def _chat_ollama(
        self, provider: LLMProvider, messages: list[dict]
    ) -> Optional[str]:
        """Chat via Ollama's native /api/chat endpoint (streaming)."""
        url = f"{provider.base_url.rstrip('/')}/api/chat"
        payload = {
            "model": provider.model,
            "messages": messages,
            "stream": True,
            "options": {
                "num_gpu": getattr(config, "OLLAMA_NUM_GPU", -1),
                "num_ctx": getattr(config, "OLLAMA_NUM_CTX", 2048),
            },
        }

        try:
            response = self._http_session.post(
                url, json=payload, timeout=provider.timeout, stream=True
            )
            response.raise_for_status()

            parts: list[str] = []
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    chunk = _json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        parts.append(token)
                    if chunk.get("done"):
                        break
                except _json.JSONDecodeError:
                    continue

            provider._healthy = True
            provider._last_error = None
            return "".join(parts) if parts else None

        except requests.ConnectionError as exc:
            logger.error(
                "[OLLAMA] Cannot connect to %s — is it running?", provider.base_url
            )
            provider._healthy = False
            provider._last_error = f"connection: {exc}"
            raise _TemporaryError(str(exc)) from exc

        except Exception as exc:  # noqa: BLE001
            logger.error("[OLLAMA] Request failed: %s", exc)
            provider._healthy = False
            provider._last_error = str(exc)
            raise _TemporaryError(str(exc)) from exc

    def _stream_ollama(
        self, provider: LLMProvider, messages: list[dict]
    ) -> Generator[str, None, None]:
        """Stream tokens from Ollama's native API."""
        url = f"{provider.base_url.rstrip('/')}/api/chat"
        payload = {
            "model": provider.model,
            "messages": messages,
            "stream": True,
            "options": {
                "num_gpu": getattr(config, "OLLAMA_NUM_GPU", -1),
                "num_ctx": getattr(config, "OLLAMA_NUM_CTX", 2048),
            },
        }

        try:
            response = self._http_session.post(
                url, json=payload, timeout=provider.timeout, stream=True
            )
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    chunk = _json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield token
                    if chunk.get("done"):
                        break
                except _json.JSONDecodeError:
                    continue

            provider._healthy = True
            provider._last_error = None

        except requests.ConnectionError as exc:
            logger.error(
                "[OLLAMA] Cannot connect to %s — is it running?", provider.base_url
            )
            provider._healthy = False
            provider._last_error = f"connection: {exc}"
            raise _TemporaryError(str(exc)) from exc

        except Exception as exc:  # noqa: BLE001
            logger.error("[OLLAMA] Stream failed: %s", exc)
            provider._healthy = False
            provider._last_error = str(exc)
            raise _TemporaryError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def _heartbeat_loop(self) -> None:
        """Periodically check provider health."""
        while not self._heartbeat_stop.is_set():
            self._run_heartbeat()
            self._heartbeat_stop.wait(self._heartbeat_interval)

    def _run_heartbeat(self) -> None:
        """Single heartbeat pass — ping every provider."""
        for provider in self._providers:
            if provider._permanently_disabled:
                continue
            if not provider.enabled:
                continue

            try:
                if provider.is_local:
                    self._ping_ollama(provider)
                else:
                    self._ping_cloud(provider)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Heartbeat failed for %s: %s", provider.name, exc
                )

        self._resolve_active()

    def _ping_cloud(self, provider: LLMProvider) -> None:
        """HEAD request to cloud provider — check reachability."""
        try:
            start = time.monotonic()
            resp = requests.head(
                f"{provider.base_url}/models",
                timeout=2,
                headers={"Authorization": f"Bearer {provider.api_key}"},
            )
            elapsed_ms = (time.monotonic() - start) * 1000

            if resp.status_code in (401, 403):
                logger.error(
                    "[%s] Auth error on heartbeat — disabling", provider.name
                )
                provider._permanently_disabled = True
                provider._last_error = f"auth_{resp.status_code}"
                return

            if resp.status_code < 500 and elapsed_ms < 500:
                if not provider._healthy:
                    logger.info(
                        "[%s] Back online (%.0fms)", provider.name, elapsed_ms
                    )
                provider._healthy = True
                provider._last_error = None
            else:
                logger.debug(
                    "[%s] Ping slow/bad (%.0fms, %d)",
                    provider.name,
                    elapsed_ms,
                    resp.status_code,
                )
                provider._healthy = False
                provider._last_error = (
                    f"ping: {elapsed_ms:.0f}ms status={resp.status_code}"
                )
        except Exception as exc:
            logger.debug("[%s] Ping failed: %s", provider.name, exc)
            provider._healthy = False
            provider._last_error = f"ping: {exc}"

    def _ping_ollama(self, provider: LLMProvider) -> None:
        """GET /api/tags to check Ollama is alive."""
        try:
            resp = requests.get(
                f"{provider.base_url.rstrip('/')}/api/tags", timeout=3
            )
            was_healthy = provider._healthy
            provider._healthy = resp.status_code == 200
            if provider._healthy:
                provider._last_error = None
                if not was_healthy:
                    logger.info("[OLLAMA] Back online")
            else:
                provider._last_error = f"status={resp.status_code}"
        except Exception as exc:
            provider._healthy = False
            provider._last_error = f"ping: {exc}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_messages(self) -> list[dict]:
        """Prepend system prompt to history."""
        return [
            {"role": "system", "content": self.system_prompt},
            *self._history,
        ]

    def _get_ordered_providers(self) -> list[LLMProvider]:
        """Return providers in priority order, available ones first."""
        available = [p for p in self._providers if p.available]
        unavailable = [
            p
            for p in self._providers
            if not p.available and not p._permanently_disabled and p.enabled
        ]
        return available + unavailable

    def _resolve_active(self) -> None:
        """Determine the current best provider and fire change callback."""
        for p in self._providers:
            if p.available:
                self._set_active(p.name)
                return
        # Nothing healthy — pick first enabled as "active" anyway
        for p in self._providers:
            if p.enabled and not p._permanently_disabled:
                self._set_active(p.name)
                return

    def _set_active(self, name: str) -> None:
        """Update active provider and notify callback on change."""
        if name == self._active_provider:
            return
        old = self._active_provider
        self._active_provider = name
        logger.info("Active provider: %s → %s", old or "(none)", name)
        if self._on_provider_change:
            try:
                self._on_provider_change(name)
            except Exception:  # noqa: BLE001
                pass

    def _trim_history(self) -> None:
        """Keep only the last ``max_history`` exchange pairs."""
        max_messages = self.max_history * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]


# ── Sentinel exceptions for fallback control flow ─────────────────────


class _PermanentError(Exception):
    """Provider should be disabled (auth failure)."""


class _TemporaryError(Exception):
    """Provider is temporarily down — try next."""


# ── Helpers ───────────────────────────────────────────────────────────


def _get_openai_key() -> str:
    """Resolve OpenAI API key from env."""
    import os

    return os.environ.get("OPENAI_API_KEY", "")
