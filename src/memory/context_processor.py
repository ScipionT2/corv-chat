"""
Context Processor — Sub-Agent A.

Uses Gemini 1.5 Flash (via OpenRouter) to digest conversation chunks
into structured memory blocks.  Runs periodically (every N exchanges)
and on session end.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Optional

import config
from src.memory.store import MemoryBlock

logger = logging.getLogger(__name__)

CONTEXT_PROCESSOR_SYSTEM_PROMPT = """\
You are a Context Processor for Nova, a personal AI assistant.

You receive conversation exchanges and distill them into clean, structured \
memory blocks. Extract:
- Key facts stated by the user
- User preferences and corrections
- Decisions made during the conversation
- Tasks mentioned or completed
- Important context for future conversations

Output a JSON array of memory blocks:
[
  {"category": "user_preferences|facts|conversation_summaries|task_history", "content": "...", "importance": 0.0-1.0}
]

Rules:
- Be concise but complete. Never lose important details.
- Merge related information into single blocks.
- Skip greetings, filler, and trivial exchanges.
- If nothing worth storing, output an empty array: []
- Output ONLY the JSON array. No markdown fences, no extra text.
"""


class ContextProcessor:
    """Sub-Agent A — digests conversation chunks into memory blocks.

    Uses Gemini 1.5 Flash via OpenRouter.  Thread-safe.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self._api_key = api_key or config.OPENROUTER_API_KEY
        self._base_url = base_url or config.OPENROUTER_BASE_URL
        self._model = model or config.CONTEXT_PROCESSOR_MODEL
        self._lock = threading.Lock()
        self._client = None

        if self._api_key:
            self._init_client()
        else:
            logger.warning("ContextProcessor: no OpenRouter API key — disabled")

    def _init_client(self) -> None:
        try:
            import openai
            self._client = openai.OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
            )
            logger.info("ContextProcessor: client ready (model=%s)", self._model)
        except Exception as exc:
            logger.error("ContextProcessor: failed to init client: %s", exc)
            self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def process(self, conversation_chunk: list[dict[str, str]]) -> list[MemoryBlock]:
        """Digest a conversation chunk into structured memory blocks.

        Parameters
        ----------
        conversation_chunk:
            List of {role, content} message dicts to process.

        Returns
        -------
        list[MemoryBlock]
            Extracted memory blocks (may be empty).
        """
        if not self._client:
            return []

        if not conversation_chunk:
            return []

        # Format the conversation
        lines = []
        for msg in conversation_chunk:
            lines.append(f"{msg['role'].upper()}: {msg['content']}")
        conversation_text = "\n".join(lines)

        try:
            with self._lock:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": CONTEXT_PROCESSOR_SYSTEM_PROMPT},
                        {"role": "user", "content": conversation_text},
                    ],
                    temperature=0.2,
                    max_tokens=1024,
                    timeout=20,
                )

            raw = response.choices[0].message.content.strip()
            return self._parse_response(raw)

        except Exception as exc:
            logger.warning("ContextProcessor: process failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_response(raw: str) -> list[MemoryBlock]:
        """Parse the processor's JSON array response."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            first_nl = cleaned.index("\n")
            cleaned = cleaned[first_nl + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].rstrip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.warning("ContextProcessor: JSON parse error: %s\nRaw: %s", exc, raw[:300])
            return []

        if not isinstance(data, list):
            data = [data] if isinstance(data, dict) else []

        blocks: list[MemoryBlock] = []
        for item in data:
            if isinstance(item, dict) and "content" in item:
                blocks.append(MemoryBlock(
                    category=item.get("category", "facts"),
                    content=item["content"],
                    importance=float(item.get("importance", 0.5)),
                ))
        return blocks
