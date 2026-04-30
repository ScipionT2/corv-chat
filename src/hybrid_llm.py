"""
Hybrid LLM Client — Auto-switches between Cloud (OpenAI) and Local (Ollama).

Heartbeat check: if internet ping exceeds 500ms or fails, routes to local.
Exposes a single .chat() interface identical to OllamaClient.
"""

from __future__ import annotations

import logging
import time
import threading
from typing import Optional, Callable

import requests

import config

logger = logging.getLogger(__name__)

# Try importing openai
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.info("openai package not installed — cloud mode unavailable")


class HybridLLMClient:
    """
    Routes between OpenAI (cloud) and Ollama (local) based on connectivity.

    Parameters
    ----------
    openai_api_key:
        OpenAI API key. If None, tries OPENAI_API_KEY env var.
    openai_model:
        Cloud model (default: gpt-4o).
    ollama_base_url:
        Local Ollama URL.
    ollama_model:
        Local model tag.
    ping_threshold_ms:
        If ping > this, switch to local.
    heartbeat_interval_s:
        How often to check connectivity (seconds).
    on_mode_change:
        Callback when mode changes: fn("cloud") or fn("local").
    """

    def __init__(
        self,
        openai_api_key: Optional[str] = None,
        openai_model: str = "gpt-4o",
        ollama_base_url: str = config.OLLAMA_BASE_URL,
        ollama_model: str = config.OLLAMA_MODEL,
        system_prompt: str = config.LLM_SYSTEM_PROMPT,
        max_history: int = config.LLM_MAX_HISTORY,
        ping_threshold_ms: int = 500,
        heartbeat_interval_s: float = 30.0,
        on_mode_change: Optional[Callable[[str], None]] = None,
    ):
        self.openai_model = openai_model
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.ollama_model = ollama_model
        self.system_prompt = system_prompt
        self.max_history = max_history
        self.ping_threshold_ms = ping_threshold_ms
        self.heartbeat_interval_s = heartbeat_interval_s
        self._on_mode_change = on_mode_change

        self._history: list[dict[str, str]] = []
        self._mode: str = "local"  # Start local, prove cloud later
        self._lock = threading.Lock()

        # OpenAI client
        self._openai_client = None
        if OPENAI_AVAILABLE:
            import os
            key = openai_api_key or os.environ.get("OPENAI_API_KEY")
            if key:
                self._openai_client = openai.OpenAI(api_key=key)
                logger.info("OpenAI client initialized (cloud mode available)")
            else:
                logger.info("No OPENAI_API_KEY — cloud mode unavailable")

        # Heartbeat thread
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="hybrid-llm-heartbeat",
            daemon=True,
        )

    @property
    def mode(self) -> str:
        """Current mode: 'cloud' or 'local'."""
        return self._mode

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Start the heartbeat checker."""
        self._heartbeat_stop.clear()
        self._heartbeat_thread.start()
        # Initial check
        self._check_connectivity()

    def stop(self):
        """Stop the heartbeat checker."""
        self._heartbeat_stop.set()

    # ------------------------------------------------------------------
    # Chat API (compatible with OllamaClient)
    # ------------------------------------------------------------------

    def chat(self, user_message: str) -> Optional[str]:
        """Send a message. Routes to cloud or local based on current mode."""
        self._history.append({"role": "user", "content": user_message})

        if self._mode == "cloud" and self._openai_client:
            reply = self._chat_cloud(user_message)
        else:
            reply = self._chat_local(user_message)

        if reply:
            self._history.append({"role": "assistant", "content": reply})
        else:
            # Remove unanswered user message
            if self._history and self._history[-1]["role"] == "user":
                self._history.pop()

        self._trim_history()
        return reply

    def clear_history(self):
        """Clear conversation history."""
        self._history.clear()

    @property
    def history(self) -> list[dict[str, str]]:
        return list(self._history)

    # ------------------------------------------------------------------
    # Cloud (OpenAI)
    # ------------------------------------------------------------------

    def _chat_cloud(self, user_message: str) -> Optional[str]:
        """Chat via OpenAI API."""
        try:
            messages = [
                {"role": "system", "content": self.system_prompt},
                *self._history,
            ]

            response = self._openai_client.chat.completions.create(
                model=self.openai_model,
                messages=messages,
                timeout=30,
            )

            reply = response.choices[0].message.content
            logger.info("[CLOUD] Reply: %s", reply[:100] if reply else "empty")
            return reply

        except Exception as exc:
            logger.warning("[CLOUD] Failed, falling back to local: %s", exc)
            # Fallback to local on failure
            self._set_mode("local")
            return self._chat_local(user_message)

    # ------------------------------------------------------------------
    # Local (Ollama)
    # ------------------------------------------------------------------

    def _chat_local(self, user_message: str) -> Optional[str]:
        """Chat via local Ollama."""
        import json as _json

        messages = [
            {"role": "system", "content": self.system_prompt},
            *self._history,
        ]

        url = f"{self.ollama_base_url}/api/chat"
        payload = {
            "model": self.ollama_model,
            "messages": messages,
            "stream": True,
            "options": {
                "num_gpu": getattr(config, "OLLAMA_NUM_GPU", -1),
            },
        }

        try:
            response = requests.post(url, json=payload, timeout=config.OLLAMA_TIMEOUT, stream=True)
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

            reply = "".join(parts) if parts else None
            if reply:
                logger.info("[LOCAL] Reply: %s", reply[:100])
            return reply

        except requests.ConnectionError:
            logger.error("Cannot connect to Ollama at %s", self.ollama_base_url)
            return None
        except Exception as exc:
            logger.error("[LOCAL] LLM request failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Heartbeat / Connectivity
    # ------------------------------------------------------------------

    def _heartbeat_loop(self):
        """Periodically check connectivity."""
        while not self._heartbeat_stop.is_set():
            self._check_connectivity()
            self._heartbeat_stop.wait(self.heartbeat_interval_s)

    def _check_connectivity(self):
        """Ping check — if >500ms or fail, switch to local."""
        if not self._openai_client:
            self._set_mode("local")
            return

        try:
            start = time.monotonic()
            # Light HEAD request to OpenAI API (fast check)
            resp = requests.head(
                "https://api.openai.com/v1/models",
                timeout=2,
                headers={"Authorization": f"Bearer {self._openai_client.api_key}"},
            )
            elapsed_ms = (time.monotonic() - start) * 1000

            if resp.status_code < 500 and elapsed_ms < self.ping_threshold_ms:
                self._set_mode("cloud")
            else:
                logger.info("Ping too slow (%.0fms) or bad status (%d) — local mode",
                            elapsed_ms, resp.status_code)
                self._set_mode("local")

        except Exception as exc:
            logger.debug("Connectivity check failed: %s — local mode", exc)
            self._set_mode("local")

    def _set_mode(self, new_mode: str):
        """Switch mode and notify callback."""
        if new_mode == self._mode:
            return
        old = self._mode
        self._mode = new_mode
        logger.info("LLM mode: %s → %s", old, new_mode)
        if self._on_mode_change:
            try:
                self._on_mode_change(new_mode)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _trim_history(self):
        max_messages = self.max_history * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]
