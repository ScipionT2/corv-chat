"""
Ollama LLM client for EP Agent local chat completions.

Communicates with a running Ollama instance over HTTP and maintains a
rolling conversation history for multi-turn context.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)


class OllamaClient:
    """HTTP client for the Ollama ``/api/chat`` endpoint.

    Parameters
    ----------
    base_url:
        Ollama server base URL.
    model:
        Model tag to use for completions.
    system_prompt:
        System-level instruction prepended to every request.
    max_history:
        Maximum number of user/assistant exchange *pairs* to retain.
    timeout:
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = config.OLLAMA_BASE_URL,
        model: str = config.OLLAMA_MODEL,
        system_prompt: str = config.LLM_SYSTEM_PROMPT,
        max_history: int = config.LLM_MAX_HISTORY,
        timeout: int = config.OLLAMA_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.system_prompt = system_prompt
        self.max_history = max_history
        self.timeout = timeout

        self._history: list[dict[str, str]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(self, user_message: str) -> Optional[str]:
        """Send a user message and return the assistant's reply.

        The conversation history is automatically maintained and trimmed.

        Parameters
        ----------
        user_message:
            The user's transcribed speech.

        Returns
        -------
        str or None
            The assistant reply, or ``None`` on failure.
        """
        self._history.append({"role": "user", "content": user_message})

        messages = [
            {"role": "system", "content": self.system_prompt},
            *self._history,
        ]

        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {
                "num_gpu": getattr(config, "OLLAMA_NUM_GPU", -1),
                "num_ctx": getattr(config, "OLLAMA_NUM_CTX", 2048),
            },
        }

        logger.debug("POST %s model=%s", url, self.model)

        try:
            response = requests.post(url, json=payload, timeout=self.timeout, stream=True)
            response.raise_for_status()

            full_reply = self._read_stream(response)
            if full_reply:
                self._history.append({"role": "assistant", "content": full_reply})
                logger.info("LLM reply: %s", full_reply[:120])
            self._trim_history()
            return full_reply

        except requests.ConnectionError:
            logger.error(
                "Cannot connect to Ollama at %s — is it running?", self.base_url
            )
            # Remove the unanswered user message
            if self._history and self._history[-1]["role"] == "user":
                self._history.pop()
            return None
        except Exception as exc:  # noqa: BLE001
            logger.error("LLM request failed: %s", exc)
            if self._history and self._history[-1]["role"] == "user":
                self._history.pop()
            return None

    def chat_stream(self, user_message: str):
        """Generator that yields tokens as they stream in. Appends to history when done."""
        self._history.append({"role": "user", "content": user_message})

        messages = [
            {"role": "system", "content": self.system_prompt},
            *self._history,
        ]

        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {
                "num_gpu": getattr(config, "OLLAMA_NUM_GPU", -1),
                "num_ctx": getattr(config, "OLLAMA_NUM_CTX", 2048),
            },
        }

        logger.debug("POST %s model=%s (streaming)", url, self.model)

        import json as _json

        try:
            response = requests.post(url, json=payload, timeout=self.timeout, stream=True)
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
                        yield token
                    if chunk.get("done"):
                        break
                except _json.JSONDecodeError:
                    continue

            full_reply = "".join(parts)
            if full_reply:
                self._history.append({"role": "assistant", "content": full_reply})
                logger.info("LLM stream reply: %s", full_reply[:120])
            self._trim_history()

        except requests.ConnectionError:
            logger.error(
                "Cannot connect to Ollama at %s — is it running?", self.base_url
            )
            if self._history and self._history[-1]["role"] == "user":
                self._history.pop()
        except Exception as exc:  # noqa: BLE001
            logger.error("LLM stream request failed: %s", exc)
            if self._history and self._history[-1]["role"] == "user":
                self._history.pop()

    def inject_context(self, role: str, content: str) -> None:
        """Inject a message into history without triggering an LLM call.

        Useful for adding context (e.g., vision results) that the LLM
        should be aware of in future turns.

        Parameters
        ----------
        role:
            Message role ('assistant', 'user', or 'system').
        content:
            The message content to inject.
        """
        self._history.append({"role": role, "content": content})
        self._trim_history()
        logger.debug("Injected context [%s]: %s", role, content[:80])

    def clear_history(self) -> None:
        """Erase the conversation history."""
        self._history.clear()
        logger.info("Conversation history cleared")

    @property
    def history(self) -> list[dict[str, str]]:
        """Return a copy of the current conversation history."""
        return list(self._history)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _trim_history(self) -> None:
        """Keep only the last ``max_history`` exchange pairs."""
        max_messages = self.max_history * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]

    @staticmethod
    def _read_stream(response: requests.Response) -> Optional[str]:
        """Consume a streaming Ollama response and concatenate the tokens."""
        import json as _json  # noqa: WPS433

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
        return "".join(parts) if parts else None
