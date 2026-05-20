"""
Execution Agent — Sub-Agent B.

Uses GPT-4o-mini (via OpenRouter) to generate the final user-facing response.
Replaces direct LLM chat — receives relevant memory context from the Architect
and produces natural, memory-aware responses.
"""

from __future__ import annotations

import logging
import threading
from typing import Generator, Optional

import config

logger = logging.getLogger(__name__)

_MEMORY_AUGMENT = (
    "You have access to relevant memories about the user. "
    "Use them naturally without explicitly mentioning 'my memory says...' or "
    "'according to my records...'. Just know things and be helpful. "
    "Be concise and direct."
)


class ExecutionAgent:
    """Sub-Agent B — generates the final response.

    Uses GPT-4o-mini via OpenRouter.  Provides both blocking and
    streaming interfaces.  Thread-safe.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> None:
        self._api_key = api_key or config.OPENROUTER_API_KEY
        self._base_url = base_url or config.OPENROUTER_BASE_URL
        self._model = model or config.EXECUTION_MODEL
        self._system_prompt = system_prompt or config.LLM_SYSTEM_PROMPT
        self._lock = threading.Lock()
        self._client = None

        if self._api_key:
            self._init_client()
        else:
            logger.warning("ExecutionAgent: no OpenRouter API key — disabled")

    def _init_client(self) -> None:
        try:
            import openai
            self._client = openai.OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
            )
            logger.info("ExecutionAgent: client ready (model=%s)", self._model)
        except Exception as exc:
            logger.error("ExecutionAgent: failed to init client: %s", exc)
            self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def _build_system_prompt(self, memory_context: str) -> str:
        """Build the full system prompt with memory context injected."""
        parts = [self._system_prompt, _MEMORY_AUGMENT]
        if memory_context:
            parts.append(
                f"\n## Relevant Memories\n{memory_context}"
            )
        return "\n\n".join(parts)

    def _build_messages(
        self,
        user_message: str,
        memory_context: str,
        conversation_history: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Build the messages list for the API call."""
        system = self._build_system_prompt(memory_context)
        messages = [{"role": "system", "content": system}]
        messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_message})
        return messages

    def respond(
        self,
        user_message: str,
        memory_context: str = "",
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> Optional[str]:
        """Generate a complete response (blocking).

        Parameters
        ----------
        user_message:
            The user's latest message.
        memory_context:
            Formatted text of relevant memory blocks from the store.
        conversation_history:
            Recent conversation exchanges (list of {role, content}).

        Returns
        -------
        str or None
            The assistant's response, or None on failure.
        """
        if not self._client:
            return None

        messages = self._build_messages(
            user_message, memory_context, conversation_history or [],
        )

        try:
            with self._lock:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=1024,
                    timeout=30,
                )
            reply = response.choices[0].message.content
            logger.debug("ExecutionAgent: reply (%d chars)", len(reply) if reply else 0)
            return reply
        except Exception as exc:
            logger.warning("ExecutionAgent: respond failed: %s", exc)
            return None

    def respond_stream(
        self,
        user_message: str,
        memory_context: str = "",
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> Generator[str, None, None]:
        """Stream response tokens.

        Yields
        ------
        str
            Individual tokens as they arrive.
        """
        if not self._client:
            return

        messages = self._build_messages(
            user_message, memory_context, conversation_history or [],
        )

        try:
            with self._lock:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=1024,
                    timeout=30,
                    stream=True,
                )

            for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield delta.content

        except Exception as exc:
            logger.warning("ExecutionAgent: respond_stream failed: %s", exc)
