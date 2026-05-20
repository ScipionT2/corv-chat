"""
Memory Router — Orchestrates the 3-tier memory agent system.

Flow per user message:
1. Architect analyzes message + history + memory summary
2. MemoryStore retrieves relevant blocks based on Architect's queries
3. Execution Agent generates response with memory context
4. Every N exchanges, Context Processor digests history into memory
5. Architect store decisions are saved immediately

Falls back to the existing LLM client (Ollama / Hybrid) when any
memory agent is unavailable.
"""

from __future__ import annotations

import logging
import threading
from typing import Generator, Optional

import config
from src.memory.architect import ArchitectDecision, MemoryArchitect
from src.memory.context_processor import ContextProcessor
from src.memory.execution import ExecutionAgent
from src.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# How many exchanges between Context Processor digestion runs
_DIGEST_INTERVAL = 5


class MemoryRouter:
    """Orchestrator that wires the 3-tier memory system together.

    Drop-in replacement for HybridLLMClient / OllamaClient — exposes
    the same ``chat()``, ``chat_stream()``, ``inject_context()``,
    ``clear_history()``, and ``history`` interfaces.

    Parameters
    ----------
    fallback_llm:
        Existing LLM client (HybridLLMClient or OllamaClient) used when
        the memory agents are unavailable.
    """

    def __init__(self, fallback_llm=None) -> None:
        self._fallback = fallback_llm
        self._lock = threading.Lock()

        # Conversation history (mirroring the interface of HybridLLMClient)
        self._history: list[dict[str, str]] = []
        self._max_history = config.LLM_MAX_HISTORY

        # Exchange counter for periodic digestion
        self._exchange_count = 0

        # 3-tier agents
        self.store = MemoryStore()
        self.architect = MemoryArchitect()
        self.context_processor = ContextProcessor()
        self.execution = ExecutionAgent()

        self._ready = (
            self.architect.available
            and self.execution.available
        )
        if self._ready:
            logger.info("MemoryRouter: all agents ready — memory system active")
        else:
            logger.warning(
                "MemoryRouter: agents not fully available — using fallback LLM"
            )

    @property
    def available(self) -> bool:
        """True if the memory agent pipeline is operational."""
        return self._ready

    # ------------------------------------------------------------------
    # Compatible interface (drop-in for HybridLLMClient)
    # ------------------------------------------------------------------

    @property
    def history(self) -> list[dict[str, str]]:
        return list(self._history)

    def inject_context(self, role: str, content: str) -> None:
        """Inject a message into history (e.g., vision results)."""
        self._history.append({"role": role, "content": content})
        self._trim_history()

    def clear_history(self) -> None:
        """Clear conversation history. Triggers a final digestion pass."""
        if self._history and self.context_processor.available:
            self._digest_history()
        self._history.clear()
        self._exchange_count = 0

    # Lifecycle stubs — MemoryRouter doesn't need start/stop
    # but the pipeline may call them if they exist.
    def start(self) -> None:
        pass

    def stop(self) -> None:
        # Digest remaining history on shutdown
        if self._history and self.context_processor.available:
            try:
                self._digest_history()
            except Exception as exc:
                logger.debug("Final digestion failed: %s", exc)

    # ------------------------------------------------------------------
    # Chat (blocking)
    # ------------------------------------------------------------------

    def chat(self, user_message: str) -> Optional[str]:
        """Process a user message through the memory pipeline.

        Falls back to the existing LLM on any failure.
        """
        if not self._ready:
            return self._chat_fallback(user_message)

        try:
            return self._chat_with_memory(user_message)
        except Exception as exc:
            logger.warning("MemoryRouter: chat failed, falling back: %s", exc)
            return self._chat_fallback(user_message)

    def _chat_with_memory(self, user_message: str) -> Optional[str]:
        """Full memory-augmented chat flow (blocking)."""
        # 1. Architect analyzes
        decision = self.architect.analyze(
            user_message=user_message,
            recent_history=self._history[-6:],
            memory_summary=self.store.get_summary(),
        )

        # 2. Retrieve relevant memories
        memory_context = self._retrieve_context(decision)

        # 3. Store anything the Architect decided to remember
        self._store_decisions(decision)

        # 4. Execution Agent generates response
        reply = self.execution.respond(
            user_message=user_message,
            memory_context=memory_context,
            conversation_history=self._history[-self._max_history * 2:],
        )

        if reply is None:
            # Execution agent failed — fallback
            return self._chat_fallback(user_message)

        # 5. Update history
        self._history.append({"role": "user", "content": user_message})
        self._history.append({"role": "assistant", "content": reply})
        self._trim_history()

        # 6. Periodic digestion
        self._exchange_count += 1
        if self._exchange_count >= _DIGEST_INTERVAL:
            self._maybe_digest()

        return reply

    # ------------------------------------------------------------------
    # Chat stream (generator)
    # ------------------------------------------------------------------

    def chat_stream(self, user_message: str) -> Generator[str, None, None]:
        """Stream response tokens through the memory pipeline.

        Falls back to the existing LLM's streaming on failure.
        """
        if not self._ready:
            yield from self._stream_fallback(user_message)
            return

        try:
            yield from self._stream_with_memory(user_message)
        except Exception as exc:
            logger.warning("MemoryRouter: chat_stream failed, falling back: %s", exc)
            yield from self._stream_fallback(user_message)

    def _stream_with_memory(self, user_message: str) -> Generator[str, None, None]:
        """Full memory-augmented streaming flow."""
        # 1. Architect analyzes
        decision = self.architect.analyze(
            user_message=user_message,
            recent_history=self._history[-6:],
            memory_summary=self.store.get_summary(),
        )

        # 2. Retrieve relevant memories
        memory_context = self._retrieve_context(decision)

        # 3. Store architect decisions immediately
        self._store_decisions(decision)

        # 4. Stream from Execution Agent
        self._history.append({"role": "user", "content": user_message})
        parts: list[str] = []

        try:
            for token in self.execution.respond_stream(
                user_message=user_message,
                memory_context=memory_context,
                conversation_history=self._history[-(self._max_history * 2):],
            ):
                parts.append(token)
                yield token
        except Exception as exc:
            logger.warning("ExecutionAgent stream failed: %s", exc)
            # Remove the user message we just added
            if self._history and self._history[-1].get("role") == "user":
                self._history.pop()
            # Try fallback
            yield from self._stream_fallback(user_message)
            return

        full_reply = "".join(parts)
        if full_reply:
            self._history.append({"role": "assistant", "content": full_reply})
        else:
            # Remove unanswered user message
            if self._history and self._history[-1].get("role") == "user":
                self._history.pop()
        self._trim_history()

        # 5. Periodic digestion
        self._exchange_count += 1
        if self._exchange_count >= _DIGEST_INTERVAL:
            self._maybe_digest()

    # ------------------------------------------------------------------
    # Memory helpers
    # ------------------------------------------------------------------

    def _retrieve_context(self, decision: ArchitectDecision) -> str:
        """Search the store using the Architect's retrieve queries.

        Returns formatted text suitable for injection into the system
        prompt.
        """
        if not decision.retrieve_queries:
            return ""

        all_blocks = []
        seen_ids: set[str] = set()
        for query in decision.retrieve_queries:
            for block in self.store.search(query):
                if block.id not in seen_ids:
                    seen_ids.add(block.id)
                    all_blocks.append(block)

        if not all_blocks:
            return ""

        # Format for the Execution Agent
        lines: list[str] = []
        for b in all_blocks[:config.MEMORY_RELEVANCE_TOP_K]:
            lines.append(f"- [{b.category}] {b.content}")
        return "\n".join(lines)

    def _store_decisions(self, decision: ArchitectDecision) -> None:
        """Persist the Architect's store decisions to the MemoryStore."""
        for item in decision.store_items:
            try:
                self.store.save_block(
                    category=item.category,
                    content=item.content,
                    importance=item.importance,
                )
            except Exception as exc:
                logger.warning("Failed to store memory block: %s", exc)

    def _maybe_digest(self) -> None:
        """Run Context Processor digestion in a background thread."""
        if not self.context_processor.available:
            self._exchange_count = 0
            return

        # Copy history for background processing
        chunk = list(self._history)
        self._exchange_count = 0

        thread = threading.Thread(
            target=self._digest_worker,
            args=(chunk,),
            name="memory-digest",
            daemon=True,
        )
        thread.start()

    def _digest_worker(self, chunk: list[dict[str, str]]) -> None:
        """Background: run Context Processor and save extracted blocks."""
        try:
            blocks = self.context_processor.process(chunk)
            for block in blocks:
                self.store.save_block(
                    category=block.category,
                    content=block.content,
                    importance=block.importance,
                )
            if blocks:
                logger.info("Context Processor: digested %d blocks from %d messages",
                            len(blocks), len(chunk))
        except Exception as exc:
            logger.warning("Context Processor digest failed: %s", exc)

    def _digest_history(self) -> None:
        """Synchronous digestion of current history (used on shutdown/clear)."""
        try:
            blocks = self.context_processor.process(list(self._history))
            for block in blocks:
                self.store.save_block(
                    category=block.category,
                    content=block.content,
                    importance=block.importance,
                )
            if blocks:
                logger.info("Final digestion: stored %d blocks", len(blocks))
        except Exception as exc:
            logger.warning("Final digestion failed: %s", exc)

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    def _chat_fallback(self, user_message: str) -> Optional[str]:
        """Use the existing LLM client as fallback."""
        if self._fallback is None:
            logger.error("MemoryRouter: no fallback LLM configured")
            return None
        logger.debug("MemoryRouter: using fallback LLM for chat")
        return self._fallback.chat(user_message)

    def _stream_fallback(self, user_message: str) -> Generator[str, None, None]:
        """Use the existing LLM client's streaming as fallback."""
        if self._fallback is None:
            logger.error("MemoryRouter: no fallback LLM configured")
            return
        logger.debug("MemoryRouter: using fallback LLM for streaming")
        if hasattr(self._fallback, "chat_stream"):
            yield from self._fallback.chat_stream(user_message)
        else:
            reply = self._fallback.chat(user_message)
            if reply:
                yield reply

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _trim_history(self) -> None:
        """Keep history within bounds."""
        max_msgs = self._max_history * 2
        if len(self._history) > max_msgs:
            self._history = self._history[-max_msgs:]
