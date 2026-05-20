"""
Markdown-based Memory Storage.

Stores memories as markdown files in ~/.nova/memory/ organized by category.
Each file corresponds to a category (user_preferences, facts, conversation_summaries,
task_history). Blocks within files are separated by ``---`` with timestamps.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)


@dataclass
class MemoryBlock:
    """A single memory block with metadata."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    category: str = ""
    content: str = ""
    timestamp: float = field(default_factory=time.time)
    importance: float = 0.5

    @property
    def age_hours(self) -> float:
        return (time.time() - self.timestamp) / 3600.0


# Canonical category slugs
CATEGORIES = [
    "user_preferences",
    "facts",
    "conversation_summaries",
    "task_history",
]


def _slug(category: str) -> str:
    """Normalise a category name to a safe filename slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")
    return slug or "general"


class MemoryStore:
    """Markdown-file backed memory storage.

    One ``.md`` file per category inside ``memory_dir``.  Blocks are
    delimited by ``---`` lines and carry an ``id`` + ISO timestamp in a
    HTML comment header.

    Thread-safe: all public methods acquire ``_lock``.
    """

    def __init__(
        self,
        memory_dir: Optional[str] = None,
        max_blocks: Optional[int] = None,
        top_k: Optional[int] = None,
    ) -> None:
        self.memory_dir = Path(
            memory_dir or config.MEMORY_DIR
        ).expanduser()
        self.max_blocks = max_blocks or config.MEMORY_MAX_BLOCKS
        self.top_k = top_k or config.MEMORY_RELEVANCE_TOP_K
        self._lock = threading.Lock()

        # In-memory index: category -> list[MemoryBlock]
        self._index: dict[str, list[MemoryBlock]] = {}

        # Ensure directory exists
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Load existing files
        self._load_all()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_block(self, category: str, content: str, importance: float = 0.5) -> MemoryBlock:
        """Create and persist a new memory block.

        Returns the created ``MemoryBlock``.
        """
        block = MemoryBlock(
            category=_slug(category),
            content=content.strip(),
            importance=importance,
        )
        with self._lock:
            cat = block.category
            self._index.setdefault(cat, []).append(block)
            self._enforce_limit(cat)
            self._write_category(cat)
        logger.debug("Saved block %s in [%s]: %s", block.id, cat, content[:60])
        return block

    def search(self, query: str, top_k: Optional[int] = None) -> list[MemoryBlock]:
        """Keyword + recency search across all categories.

        Returns up to ``top_k`` blocks sorted by relevance score
        (higher is better).
        """
        k = top_k or self.top_k
        query_lower = query.lower()
        keywords = set(re.findall(r"\w+", query_lower))

        scored: list[tuple[float, MemoryBlock]] = []

        with self._lock:
            for blocks in self._index.values():
                for block in blocks:
                    score = self._score(block, keywords, query_lower)
                    if score > 0:
                        scored.append((score, block))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [b for _, b in scored[:k]]

    def load_all(self) -> dict[str, list[MemoryBlock]]:
        """Return a copy of the full index."""
        with self._lock:
            return {cat: list(blocks) for cat, blocks in self._index.items()}

    def get_summary(self) -> str:
        """Return a compact text summary of stored memory categories and counts."""
        with self._lock:
            if not self._index:
                return "No memories stored yet."
            lines = []
            for cat, blocks in sorted(self._index.items()):
                if blocks:
                    latest = max(b.timestamp for b in blocks)
                    age = (time.time() - latest) / 3600.0
                    lines.append(
                        f"- {cat}: {len(blocks)} blocks (latest {age:.1f}h ago)"
                    )
            return "\n".join(lines) if lines else "No memories stored yet."

    def delete_block(self, block_id: str) -> bool:
        """Delete a block by id. Returns True if found and deleted."""
        with self._lock:
            for cat, blocks in self._index.items():
                for i, b in enumerate(blocks):
                    if b.id == block_id:
                        blocks.pop(i)
                        self._write_category(cat)
                        logger.debug("Deleted block %s from [%s]", block_id, cat)
                        return True
        return False

    def block_count(self) -> int:
        """Total number of blocks across all categories."""
        with self._lock:
            return sum(len(b) for b in self._index.values())

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _score(block: MemoryBlock, keywords: set[str], query_lower: str) -> float:
        """Compute a relevance score for a block against a query.

        Combines keyword overlap and recency.
        """
        content_lower = block.content.lower()

        # Keyword match ratio
        if not keywords:
            kw_score = 0.0
        else:
            hits = sum(1 for kw in keywords if kw in content_lower)
            kw_score = hits / len(keywords)

        # Exact substring bonus
        if query_lower in content_lower:
            kw_score += 0.3

        if kw_score == 0:
            return 0.0

        # Recency decay: half-life of 48 hours
        age_h = block.age_hours
        recency = 1.0 / (1.0 + age_h / 48.0)

        # Importance weight
        importance = block.importance

        return kw_score * 0.6 + recency * 0.25 + importance * 0.15

    # ------------------------------------------------------------------
    # Persistence (markdown files)
    # ------------------------------------------------------------------

    _BLOCK_HEADER_RE = re.compile(
        r"<!--\s*id:(\S+)\s+ts:([\d.]+)\s+imp:([\d.]+)\s*-->"
    )

    def _load_all(self) -> None:
        """Load all ``.md`` files from the memory directory."""
        self._index.clear()
        for path in sorted(self.memory_dir.glob("*.md")):
            cat = path.stem
            blocks = self._parse_file(path, cat)
            if blocks:
                self._index[cat] = blocks
        total = sum(len(b) for b in self._index.values())
        if total:
            logger.info(
                "Loaded %d memory blocks across %d categories from %s",
                total, len(self._index), self.memory_dir,
            )

    def _parse_file(self, path: Path, category: str) -> list[MemoryBlock]:
        """Parse a category markdown file into memory blocks."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read %s: %s", path, exc)
            return []

        blocks: list[MemoryBlock] = []
        # Split on --- separator lines
        raw_blocks = re.split(r"\n---\n", text)
        for raw in raw_blocks:
            raw = raw.strip()
            if not raw:
                continue
            m = self._BLOCK_HEADER_RE.search(raw)
            if m:
                block_id = m.group(1)
                ts = float(m.group(2))
                imp = float(m.group(3))
                content = raw[m.end():].strip()
            else:
                block_id = uuid.uuid4().hex[:12]
                ts = time.time()
                imp = 0.5
                content = raw

            if content:
                blocks.append(MemoryBlock(
                    id=block_id,
                    category=category,
                    content=content,
                    timestamp=ts,
                    importance=imp,
                ))
        return blocks

    def _write_category(self, category: str) -> None:
        """Write all blocks for a category to its markdown file.

        Must be called while holding ``_lock``.
        """
        blocks = self._index.get(category, [])
        path = self.memory_dir / f"{category}.md"

        if not blocks:
            # Remove empty file
            path.unlink(missing_ok=True)
            return

        parts: list[str] = []
        for b in blocks:
            header = f"<!-- id:{b.id} ts:{b.timestamp:.2f} imp:{b.importance:.2f} -->"
            parts.append(f"{header}\n{b.content}")

        try:
            path.write_text("\n---\n".join(parts) + "\n", encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to write memory file %s: %s", path, exc)

    def _enforce_limit(self, category: str) -> None:
        """Trim oldest/lowest-importance blocks when over ``max_blocks``."""
        blocks = self._index.get(category, [])
        if len(blocks) <= self.max_blocks:
            return
        # Sort by importance (asc) then timestamp (asc) — drop weakest/oldest first
        blocks.sort(key=lambda b: (b.importance, b.timestamp))
        overflow = len(blocks) - self.max_blocks
        self._index[category] = blocks[overflow:]
