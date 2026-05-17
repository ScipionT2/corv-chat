"""
Vision Analysis History for Nova.

Stores the last N analyses with metadata and thumbnails for context
continuity and debugging.
"""

from __future__ import annotations

import io
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── Paths ────────────────────────────────────────────────────────────

_HISTORY_DIR = Path(os.path.expanduser("~/.nova"))
_HISTORY_FILE = _HISTORY_DIR / "vision_history.json"
_THUMBS_DIR = _HISTORY_DIR / "vision_thumbs"

# Default max entries
DEFAULT_HISTORY_SIZE = 20


def _ensure_dirs() -> None:
    """Create history directories if they don't exist."""
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    _THUMBS_DIR.mkdir(parents=True, exist_ok=True)


def _create_thumbnail(image_bytes: bytes, max_width: int = 200) -> Optional[bytes]:
    """Create a small thumbnail from full-size screenshot bytes.

    Parameters
    ----------
    image_bytes : bytes
        Full PNG screenshot data.
    max_width : int
        Maximum thumbnail width in pixels.

    Returns
    -------
    Optional[bytes]
        Thumbnail PNG bytes or None on failure.
    """
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        ratio = max_width / img.width
        new_size = (max_width, int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except ImportError:
        logger.debug("PIL not available for thumbnail creation")
        return None
    except Exception as exc:
        logger.debug("Thumbnail creation failed: %s", exc)
        return None


def save_analysis(
    result_text: str,
    app_name: str = "",
    prompt_used: str = "",
    screenshot_bytes: Optional[bytes] = None,
    max_history: int = DEFAULT_HISTORY_SIZE,
) -> None:
    """Save a vision analysis result to history.

    Parameters
    ----------
    result_text : str
        The analysis text from the vision model.
    app_name : str
        The active application name when analysis was performed.
    prompt_used : str
        The prompt that was used for analysis.
    screenshot_bytes : Optional[bytes]
        Full screenshot PNG data (will be thumbnailed).
    max_history : int
        Maximum number of entries to retain.
    """
    _ensure_dirs()

    timestamp = time.time()
    entry: Dict[str, Any] = {
        "timestamp": timestamp,
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(timestamp)),
        "analysis": result_text,
        "app_name": app_name,
        "prompt_used": prompt_used,
        "thumbnail": None,
    }

    # Save thumbnail
    if screenshot_bytes:
        thumb_data = _create_thumbnail(screenshot_bytes)
        if thumb_data:
            thumb_filename = f"thumb_{int(timestamp * 1000)}.png"
            thumb_path = _THUMBS_DIR / thumb_filename
            try:
                thumb_path.write_bytes(thumb_data)
                entry["thumbnail"] = thumb_filename
            except Exception as exc:
                logger.debug("Failed to save thumbnail: %s", exc)

    # Load existing history
    history = _load_history()

    # Append and trim
    history.append(entry)
    if len(history) > max_history:
        # Remove old entries and their thumbnails
        removed = history[:-max_history]
        history = history[-max_history:]
        for old in removed:
            if old.get("thumbnail"):
                old_path = _THUMBS_DIR / old["thumbnail"]
                try:
                    old_path.unlink(missing_ok=True)
                except Exception:
                    pass

    # Save
    _save_history(history)
    logger.debug("Saved vision analysis to history (total: %d)", len(history))


def get_history(limit: int = DEFAULT_HISTORY_SIZE) -> List[Dict[str, Any]]:
    """Get recent analysis history.

    Parameters
    ----------
    limit : int
        Maximum number of entries to return.

    Returns
    -------
    List[Dict[str, Any]]
        List of history entries (most recent last).
    """
    history = _load_history()
    return history[-limit:]


def clear_history() -> None:
    """Clear all analysis history and thumbnails."""
    _ensure_dirs()

    # Remove all thumbnails
    if _THUMBS_DIR.exists():
        for f in _THUMBS_DIR.iterdir():
            try:
                f.unlink()
            except Exception:
                pass

    # Clear history file
    _save_history([])
    logger.info("Vision history cleared")


def _load_history() -> List[Dict[str, Any]]:
    """Load history from disk."""
    if not _HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("Failed to load vision history: %s", exc)
        return []


def _save_history(history: List[Dict[str, Any]]) -> None:
    """Save history to disk."""
    try:
        _HISTORY_FILE.write_text(
            json.dumps(history, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.error("Failed to save vision history: %s", exc)
