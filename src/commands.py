"""
Built-in voice command system.

Parses user speech for special commands before sending to the LLM.
Commands like "clear history", "what time is it", "stop listening",
and "resume" are handled locally without an LLM round-trip.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)


class CommandResult(Enum):
    """Outcome of command parsing."""

    NOT_A_COMMAND = auto()
    """Input was not a recognised command — pass to LLM."""

    HANDLED = auto()
    """Command was handled; a response is available."""

    PAUSE = auto()
    """Request to pause listening."""

    RESUME = auto()
    """Request to resume listening."""

    CLEAR_HISTORY = auto()
    """Request to clear conversation history."""


class CommandResponse:
    """Response from the command parser.

    Attributes
    ----------
    result:
        The command outcome.
    message:
        An optional spoken response to the user.
    """

    def __init__(self, result: CommandResult, message: Optional[str] = None) -> None:
        self.result = result
        self.message = message

    def __repr__(self) -> str:
        return f"CommandResponse(result={self.result!r}, message={self.message!r})"


# ---------------------------------------------------------------------------
# Command patterns
# ---------------------------------------------------------------------------

# Normalise: lowercase, strip punctuation and leading "jarvis"
_PREFIX_RE = re.compile(r"^(?:jarvis[,.]?\s*)", re.IGNORECASE)

_CLEAR_HISTORY_RE = re.compile(
    r"^(?:clear|reset|delete|erase)\s+(?:the\s+)?(?:history|conversation|memory|chat)$",
    re.IGNORECASE,
)

_TIME_RE = re.compile(
    r"^what(?:"
    r"(?:'s|\s+is)\s+the\s+(?:current\s+)?time"
    r"|\s+time\s+is\s+it"
    r")(?:\s+(?:right\s+)?now)?[?]?$",
    re.IGNORECASE,
)

_STOP_RE = re.compile(
    r"^(?:stop\s+listening|pause(?:\s+listening)?|go\s+to\s+sleep|sleep)$",
    re.IGNORECASE,
)

_RESUME_RE = re.compile(
    r"^(?:resume(?:\s+listening)?|wake\s+up|start\s+listening|I'm\s+back|unpause)$",
    re.IGNORECASE,
)


def parse_command(text: str) -> CommandResponse:
    """Parse user text for built-in voice commands.

    Parameters
    ----------
    text:
        The user's transcribed speech.

    Returns
    -------
    CommandResponse
        The result of command parsing.  If ``result`` is
        ``NOT_A_COMMAND``, the text should be forwarded to the LLM.
    """
    if not text or not text.strip():
        return CommandResponse(CommandResult.NOT_A_COMMAND)

    # Strip leading "Jarvis, " prefix
    cleaned = _PREFIX_RE.sub("", text.strip()).strip()
    # Also strip trailing punctuation
    cleaned = cleaned.rstrip(".,!?")

    if _CLEAR_HISTORY_RE.match(cleaned):
        logger.info("Command: clear history")
        return CommandResponse(
            CommandResult.CLEAR_HISTORY,
            message="Conversation history has been cleared.",
        )

    if _TIME_RE.match(cleaned):
        now = datetime.now()
        time_str = now.strftime("%-I:%M %p")
        logger.info("Command: time check → %s", time_str)
        return CommandResponse(
            CommandResult.HANDLED,
            message=f"The current time is {time_str}.",
        )

    if _STOP_RE.match(cleaned):
        logger.info("Command: stop listening")
        return CommandResponse(
            CommandResult.PAUSE,
            message="Going to sleep. Say the wake word to wake me up.",
        )

    if _RESUME_RE.match(cleaned):
        logger.info("Command: resume listening")
        return CommandResponse(
            CommandResult.RESUME,
            message="I'm back and listening.",
        )

    return CommandResponse(CommandResult.NOT_A_COMMAND)
