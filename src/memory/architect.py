"""
Memory Architect — Tier 1 decision maker.

Uses Claude 3.5 Sonnet (via OpenRouter) to decide:
1. Which stored memories are relevant to retrieve
2. What new information to store from the current exchange
3. What category to file new memories under
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

import config

logger = logging.getLogger(__name__)

ARCHITECT_SYSTEM_PROMPT = """\
You are the Memory Architect for Nova, a personal AI assistant.

Your job is to decide what information from the conversation is relevant to \
remember long-term, and what stored memories are relevant to the current query.

You receive:
1. The user's current message
2. Recent conversation context (last few exchanges)
3. A summary of stored memory categories and block counts

You output a JSON object with exactly these keys:
{
  "retrieve_queries": ["keyword query 1", ...],
  "store_items": [
    {"category": "user_preferences|facts|conversation_summaries|task_history", "content": "...", "importance": 0.0-1.0}
  ],
  "reasoning": "Brief explanation of your decisions"
}

Rules:
- retrieve_queries: 0-3 short keyword queries to search stored memories. \
Empty list if nothing stored seems relevant.
- store_items: Only store genuinely useful info (preferences, facts, tasks, \
decisions, corrections). Never store greetings, small talk, or trivial exchanges.
- importance: 0.0 (trivial) to 1.0 (critical). Preferences/corrections = 0.8+, \
facts = 0.6, summaries = 0.4.
- Be selective. Most casual exchanges should produce empty store_items.
- Output ONLY the JSON object. No markdown fences, no explanation outside the JSON.
"""


@dataclass
class StoreItem:
    """An item the Architect decided to store."""
    category: str
    content: str
    importance: float = 0.5


@dataclass
class ArchitectDecision:
    """The Architect's analysis result."""
    retrieve_queries: list[str] = field(default_factory=list)
    store_items: list[StoreItem] = field(default_factory=list)
    reasoning: str = ""
    error: Optional[str] = None


class MemoryArchitect:
    """Tier 1 — decides what to remember and what to recall.

    Uses Claude 3.5 Sonnet via OpenRouter (openai-compatible API).
    Thread-safe.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self._api_key = api_key or config.OPENROUTER_API_KEY
        self._base_url = base_url or config.OPENROUTER_BASE_URL
        self._model = model or config.ARCHITECT_MODEL
        self._lock = threading.Lock()
        self._client = None

        if self._api_key:
            self._init_client()
        else:
            logger.warning("MemoryArchitect: no OpenRouter API key — disabled")

    def _init_client(self) -> None:
        """Lazily initialise the OpenAI-compatible client."""
        try:
            import openai
            self._client = openai.OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
            )
            logger.info("MemoryArchitect: client ready (model=%s)", self._model)
        except Exception as exc:
            logger.error("MemoryArchitect: failed to init client: %s", exc)
            self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def analyze(
        self,
        user_message: str,
        recent_history: list[dict[str, str]],
        memory_summary: str,
    ) -> ArchitectDecision:
        """Analyze the current exchange and decide on memory operations.

        Parameters
        ----------
        user_message:
            The user's latest message.
        recent_history:
            Last few conversation exchanges (list of {role, content} dicts).
        memory_summary:
            Compact text summary of stored memory categories.

        Returns
        -------
        ArchitectDecision
            What to retrieve and store.
        """
        if not self._client:
            return ArchitectDecision(error="Architect unavailable (no API key)")

        # Build the user prompt
        history_text = ""
        if recent_history:
            lines = []
            for msg in recent_history[-6:]:
                lines.append(f"{msg['role'].upper()}: {msg['content']}")
            history_text = "\n".join(lines)

        user_prompt = (
            f"## Current Message\n{user_message}\n\n"
            f"## Recent History\n{history_text or '(none)'}\n\n"
            f"## Stored Memory Summary\n{memory_summary or '(empty)'}"
        )

        try:
            with self._lock:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": ARCHITECT_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,
                    max_tokens=512,
                    timeout=15,
                )

            raw = response.choices[0].message.content.strip()
            return self._parse_response(raw)

        except Exception as exc:
            logger.warning("MemoryArchitect: analyze failed: %s", exc)
            return ArchitectDecision(error=str(exc))

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_response(raw: str) -> ArchitectDecision:
        """Parse the Architect's JSON response."""
        # Strip markdown fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            # Remove opening fence
            first_nl = cleaned.index("\n")
            cleaned = cleaned[first_nl + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].rstrip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.warning("Architect: failed to parse JSON: %s\nRaw: %s", exc, raw[:300])
            return ArchitectDecision(error=f"JSON parse error: {exc}")

        retrieve = data.get("retrieve_queries", [])
        if not isinstance(retrieve, list):
            retrieve = []

        store_items: list[StoreItem] = []
        for item in data.get("store_items", []):
            if isinstance(item, dict) and "content" in item:
                store_items.append(StoreItem(
                    category=item.get("category", "facts"),
                    content=item["content"],
                    importance=float(item.get("importance", 0.5)),
                ))

        return ArchitectDecision(
            retrieve_queries=[str(q) for q in retrieve[:3]],
            store_items=store_items,
            reasoning=data.get("reasoning", ""),
        )
