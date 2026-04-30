"""
Built-in voice command system for EP Agent.

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

    VISION_ANALYZE = auto()
    """Request a one-shot screen analysis."""

    VISION_TOGGLE = auto()
    """Toggle continuous analysis mode on/off."""

    SHUTDOWN = auto()
    """Immediately shut down EP Agent."""


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

# Normalise: lowercase, strip punctuation and leading wake-word prefix
_PREFIX_RE = re.compile(r"^(?:(?:jarvis|ep\s*agent)[,.]?\s*)", re.IGNORECASE)

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

_VISION_ANALYZE_RE = re.compile(
    r"^(?:what\s+(?:do\s+you|can\s+you)\s+see"
    r"|(?:analyze|analyse)\s+(?:my\s+)?(?:the\s+)?screen"
    r"|look\s+at\s+(?:my\s+)?(?:the\s+)?screen"
    r"|read\s+(?:my\s+)?(?:the\s+)?screen"
    r"|what(?:'s|\s+is)\s+on\s+(?:my\s+)?(?:the\s+)?screen"
    r"|screen\s+analysis"
    r"|(?:my\s+)?screen"  # catch partial "my screen" from truncated speech
    r"|describe\s+(?:my\s+)?(?:the\s+)?screen)"
    r"[?.]?$",
    re.IGNORECASE,
)

_SHUTDOWN_RE = re.compile(
    r"^(?:(?:ep\s*agent\s+)?off"
    r"|(?:jarvis\s+)?off"
    r"|shut\s*down"
    r"|power\s+off"
    r"|exit"
    r"|quit"
    r"|goodbye"
    r"|good\s*bye"
    r"|terminate)$",
    re.IGNORECASE,
)

_VISION_TOGGLE_RE = re.compile(
    r"^(?:(?:start|begin|enable|turn\s+on)\s+(?:analysis|screen)(?:\s+(?:analysis|mode))?"
    r"|(?:stop|end|disable|turn\s+off)\s+(?:analysis|screen)(?:\s+(?:analysis|mode))?"
    r"|toggle\s+(?:analysis|screen)(?:\s+(?:analysis|mode))?)"
    r"$",
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

    # Strip leading "EP Agent, " / legacy "Jarvis, " prefix
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
            message="Going to sleep. Say the wake word when you need me.",
        )

    if _RESUME_RE.match(cleaned):
        logger.info("Command: resume listening")
        return CommandResponse(
            CommandResult.RESUME,
            message="I'm back and listening.",
        )

    if _VISION_ANALYZE_RE.match(cleaned):
        logger.info("Command: vision analyze (one-shot)")
        return CommandResponse(
            CommandResult.VISION_ANALYZE,
            message="Analyzing your screen now.",
        )

    if _VISION_TOGGLE_RE.match(cleaned):
        logger.info("Command: toggle analysis mode")
        return CommandResponse(
            CommandResult.VISION_TOGGLE,
            message=None,  # Pipeline sets the message based on new state
        )

    if _SHUTDOWN_RE.match(cleaned):
        logger.info("Command: shutdown")
        return CommandResponse(
            CommandResult.SHUTDOWN,
            message="Shutting down. Goodbye.",
        )

    return CommandResponse(CommandResult.NOT_A_COMMAND)
