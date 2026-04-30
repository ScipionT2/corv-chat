"""
Conversation memory persistence.

Stores conversation history to a JSON file so EP Agent remembers past
conversations across restarts.  The history file is stored at
``~/.ep-agent/history.json`` by default (configurable).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)


class ConversationMemory:
    """Persist and retrieve conversation history from a JSON file.

    Parameters
    ----------
    history_file:
        Path to the JSON history file.
    max_entries:
        Maximum number of message entries (user + assistant) to keep.
        Older entries are discarded when the limit is exceeded.
    """

    def __init__(
        self,
        history_file: Optional[str] = None,
        max_entries: Optional[int] = None,
    ) -> None:
        self.history_file = Path(
            history_file or config.HISTORY_FILE
        ).expanduser()
        self.max_entries = max_entries if max_entries is not None else config.HISTORY_MAX_ENTRIES
        self._history: list[dict[str, str]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> list[dict[str, str]]:
        """Load conversation history from disk.

        Returns
        -------
        list[dict[str, str]]
            The loaded history entries.  Returns an empty list if the
            file does not exist or is corrupt.
        """
        if not self.history_file.exists():
            logger.debug("No history file at %s — starting fresh", self.history_file)
            self._history = []
            return list(self._history)

        try:
            raw = self.history_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, list):
                logger.warning("History file has unexpected format — resetting")
                self._history = []
            else:
                self._history = data
                self._trim()
            logger.info("Loaded %d history entries from %s", len(self._history), self.history_file)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load history: %s — starting fresh", exc)
            self._history = []

        return list(self._history)

    def save(self) -> None:
        """Write the current history to disk.

        Creates parent directories if they don't exist.
        """
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            self.history_file.write_text(
                json.dumps(self._history, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            logger.debug("Saved %d entries to %s", len(self._history), self.history_file)
        except OSError as exc:
            logger.error("Failed to save history: %s", exc)

    def add(self, role: str, content: str) -> None:
        """Append a message to the history and persist.

        Parameters
        ----------
        role:
            Message role (``'user'`` or ``'assistant'``).
        content:
            Message content.
        """
        self._history.append({"role": role, "content": content})
        self._trim()
        self.save()

    def clear(self) -> None:
        """Erase all history entries and persist the empty state."""
        self._history.clear()
        self.save()
        logger.info("Conversation memory cleared")

    @property
    def history(self) -> list[dict[str, str]]:
        """Return a copy of the current history."""
        return list(self._history)

    @property
    def count(self) -> int:
        """Return the number of entries in history."""
        return len(self._history)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _trim(self) -> None:
        """Trim history to ``max_entries``."""
        if len(self._history) > self.max_entries:
            self._history = self._history[-self.max_entries:]
