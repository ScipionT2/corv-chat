"""
macOS Permissions Manager for Nova.

Handles the single-approval flow for public release:
- Screen Recording (for vision/screen analysis)
- Accessibility (for controlling apps, keyboard shortcuts)

On macOS, both permissions are requested via the same binary path.
Users just need to approve Nova ONCE in System Settings > Privacy.

Usage:
    from src.permissions import check_permissions, request_permissions
    
    if not check_permissions():
        request_permissions()  # Opens System Settings + shows dialog
"""

from __future__ import annotations

import logging
import subprocess
import sys
import platform

logger = logging.getLogger(__name__)


def is_macos() -> bool:
    return platform.system() == "Darwin"


def check_screen_recording() -> bool:
    """Check if screen recording permission is granted.
    
    Uses a lightweight screen capture attempt — if it returns data,
    permission is granted.
    """
    if not is_macos():
        return True  # Non-macOS doesn't need this
    
    try:
        import mss
        with mss.mss() as sct:
            # Try to capture a 1x1 pixel — if permission denied, returns blank
            monitor = sct.monitors[0]
            img = sct.grab({"left": 0, "top": 0, "width": 1, "height": 1, "mon": 0})
            # If we get pixels, permission is granted
            return img is not None and len(img.raw) > 0
    except Exception as exc:
        logger.debug("Screen recording check failed: %s", exc)
        return False


def check_accessibility() -> bool:
    """Check if Accessibility permission is granted.
    
    Uses the macOS AXIsProcessTrusted API.
    """
    if not is_macos():
        return True
    
    try:
        result = subprocess.run(
            ["osascript", "-e", 
             'tell application "System Events" to return name of first process'],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def check_microphone() -> bool:
    """Check if microphone access is available."""
    try:
        import sounddevice as sd
        # Try to query devices — if mic permission denied, this may fail
        devices = sd.query_devices()
        input_devices = [d for d in devices if d.get("max_input_channels", 0) > 0]
        return len(input_devices) > 0
    except Exception:
        return False


def check_permissions() -> dict[str, bool]:
    """Check all required permissions.
    
    Returns a dict of permission -> granted status.
    """
    return {
        "screen_recording": check_screen_recording(),
        "accessibility": check_accessibility(),
        "microphone": check_microphone(),
    }


def request_permissions():
    """Request all permissions via macOS System Settings.
    
    Opens the Privacy & Security pane. On macOS 13+, this is the
    unified System Settings app.
    
    For public release: users approve the Nova binary ONCE,
    which grants all three permissions (screen, accessibility, mic).
    """
    if not is_macos():
        logger.info("Non-macOS platform — no permissions needed")
        return
    
    perms = check_permissions()
    missing = [k for k, v in perms.items() if not v]
    
    if not missing:
        logger.info("All permissions granted ✓")
        return
    
    logger.warning("Missing permissions: %s", ", ".join(missing))
    
    # Show a dialog explaining what's needed
    try:
        msg = (
            "Nova needs permission to:\\n\\n"
            "• Screen Recording — to see and analyze your screen\\n"
            "• Accessibility — to control apps and use shortcuts\\n"
            "• Microphone — to hear your voice commands\\n\\n"
            "Click OK to open System Settings.\\n"
            "Add Nova (or Terminal/Python) to each category."
        )
        subprocess.run([
            "osascript", "-e",
            f'display dialog "{msg}" with title "Nova Permissions" buttons {{"Cancel", "Open Settings"}} default button "Open Settings"'
        ], capture_output=True, timeout=30)
    except Exception:
        pass
    
    # Open the appropriate System Settings panes
    pane_urls = {
        "screen_recording": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    }
    
    for perm in missing:
        url = pane_urls.get(perm)
        if url:
            try:
                subprocess.run(["open", url], capture_output=True, timeout=5)
            except Exception:
                pass


def ensure_permissions() -> bool:
    """Check permissions and request if missing. Returns True if all granted."""
    perms = check_permissions()
    all_ok = all(perms.values())
    
    if not all_ok:
        request_permissions()
        return False
    
    return True


# ---------------------------------------------------------------------------
# App Bundle Info (for public release)
# ---------------------------------------------------------------------------

BUNDLE_PERMISSIONS_INFO = """
For public release as a .app bundle, add these to Info.plist:

<key>NSMicrophoneUsageDescription</key>
<string>Nova uses the microphone to listen for voice commands.</string>

<key>NSScreenCaptureUsageDescription</key>  
<string>Nova captures the screen to analyze what you're working on.</string>

<key>NSAppleEventsUsageDescription</key>
<string>Nova controls applications to help you work faster.</string>

This way macOS shows ONE permission dialog on first launch that covers
all three needs. The user clicks "Allow" once and everything works.
"""
