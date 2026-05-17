"""
macOS Active Application Detector for Nova.

Uses AppleScript via osascript to detect the frontmost app and window title.
Gracefully degrades on non-macOS platforms (returns empty strings).
"""

from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_IS_MACOS = platform.system() == "Darwin"


@dataclass
class ActiveAppInfo:
    """Information about the currently active application."""
    app_name: str = ""
    window_title: str = ""


def get_active_app() -> str:
    """Get the name of the frontmost application.

    Returns
    -------
    str
        Application name (e.g., "Visual Studio Code") or empty string on failure.
    """
    if not _IS_MACOS:
        return ""

    try:
        result = subprocess.run(
            [
                "osascript", "-e",
                'tell application "System Events" to get name of first application process whose frontmost is true',
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        logger.debug("get_active_app failed: %s", result.stderr.strip())
        return ""
    except subprocess.TimeoutExpired:
        logger.debug("get_active_app timed out")
        return ""
    except Exception as exc:
        logger.debug("get_active_app error: %s", exc)
        return ""


def get_active_window_title() -> str:
    """Get the title of the frontmost application's front window.

    Returns
    -------
    str
        Window title or empty string on failure.
    """
    if not _IS_MACOS:
        return ""

    try:
        result = subprocess.run(
            [
                "osascript", "-e",
                'tell application "System Events" to get title of front window of first application process whose frontmost is true',
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        logger.debug("get_active_window_title failed: %s", result.stderr.strip())
        return ""
    except subprocess.TimeoutExpired:
        logger.debug("get_active_window_title timed out")
        return ""
    except Exception as exc:
        logger.debug("get_active_window_title error: %s", exc)
        return ""


def get_active_app_info() -> ActiveAppInfo:
    """Get both app name and window title in one call.

    Returns
    -------
    ActiveAppInfo
        Dataclass with app_name and window_title fields.
    """
    return ActiveAppInfo(
        app_name=get_active_app(),
        window_title=get_active_window_title(),
    )


def get_window_bounds() -> Optional[Tuple[int, int, int, int]]:
    """Get the bounds (x, y, width, height) of the frontmost window.

    Returns
    -------
    Optional[Tuple[int, int, int, int]]
        (x, y, width, height) or None if detection fails.
    """
    if not _IS_MACOS:
        return None

    script = '''
    tell application "System Events"
        set frontApp to first application process whose frontmost is true
        set appWindow to front window of frontApp
        set {x, y} to position of appWindow
        set {w, h} to size of appWindow
        return (x as text) & "," & (y as text) & "," & (w as text) & "," & (h as text)
    end tell
    '''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            if len(parts) == 4:
                x, y, w, h = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                return (x, y, w, h)
        logger.debug("get_window_bounds failed: %s", result.stderr.strip())
        return None
    except subprocess.TimeoutExpired:
        logger.debug("get_window_bounds timed out")
        return None
    except (ValueError, IndexError) as exc:
        logger.debug("get_window_bounds parse error: %s", exc)
        return None
    except Exception as exc:
        logger.debug("get_window_bounds error: %s", exc)
        return None
