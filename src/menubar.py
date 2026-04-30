"""
EP Agent Menu Bar — lightweight macOS system tray integration.

Uses `rumps` for a native menu bar extra that provides:
- Status indicator (idle/listening/processing)
- Start/Stop pipeline
- Toggle vision analysis
- Show/hide overlay panel
- Status info
- Quit

This replaces the always-on overlay as the primary UI, saving GPU draw calls.
The side panel only renders when explicitly toggled.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional, Callable

logger = logging.getLogger(__name__)

try:
    import rumps
    RUMPS_AVAILABLE = True
except ImportError:
    RUMPS_AVAILABLE = False
    logger.info("rumps not installed — menu bar unavailable (pip install rumps)")


def is_available() -> bool:
    """Check if menu bar support is available."""
    return RUMPS_AVAILABLE


if RUMPS_AVAILABLE:

    # Status emoji mapping
    STATUS_ICONS = {
        "idle": "🟢",
        "listening": "🎤",
        "processing": "⚡",
        "speaking": "🔊",
        "analyzing": "👁",
        "error": "🔴",
        "sleeping": "💤",
    }

    class EPAgentMenuBar(rumps.App):
        """macOS menu bar extra for EP Agent.

        Provides lightweight status display and controls without
        rendering a full overlay window.
        """

        def __init__(
            self,
            on_start: Optional[Callable] = None,
            on_stop: Optional[Callable] = None,
            on_toggle_vision: Optional[Callable] = None,
            on_toggle_overlay: Optional[Callable] = None,
            on_quit: Optional[Callable] = None,
        ) -> None:
            super().__init__(
                "EP Agent",
                icon=None,
                title="🟢",
                quit_button=None,  # Custom quit handling
            )

            self._on_start = on_start
            self._on_stop = on_stop
            self._on_toggle_vision = on_toggle_vision
            self._on_toggle_overlay = on_toggle_overlay
            self._on_quit = on_quit
            self._state = "idle"
            self._pipeline_running = False
            self._vision_active = False

            # Build menu
            self.menu = [
                rumps.MenuItem("EP Agent", callback=None),
                None,  # separator
                rumps.MenuItem("▶ Start", callback=self._handle_start_stop),
                rumps.MenuItem("👁 Toggle Vision", callback=self._handle_vision),
                rumps.MenuItem("📋 Show Panel", callback=self._handle_overlay),
                None,  # separator
                rumps.MenuItem("Status: Idle", callback=None),
                None,  # separator
                rumps.MenuItem("Quit", callback=self._handle_quit),
            ]

        # ── Public API (thread-safe via rumps Timer) ──────────────────

        def set_state(self, state: str) -> None:
            """Update the menu bar icon/title to reflect current state."""
            self._state = state
            icon = STATUS_ICONS.get(state, "🟢")
            self.title = icon

            # Update status menu item
            status_text = f"Status: {state.capitalize()}"
            for item in self.menu.values():
                if isinstance(item, rumps.MenuItem) and item.title.startswith("Status:"):
                    item.title = status_text
                    break

        def set_pipeline_running(self, running: bool) -> None:
            """Update the start/stop menu item."""
            self._pipeline_running = running
            for item in self.menu.values():
                if isinstance(item, rumps.MenuItem):
                    if "Start" in item.title or "Stop" in item.title:
                        item.title = "⏹ Stop" if running else "▶ Start"
                        break

        def set_vision_active(self, active: bool) -> None:
            """Update vision toggle state."""
            self._vision_active = active
            for item in self.menu.values():
                if isinstance(item, rumps.MenuItem) and "Vision" in item.title:
                    item.title = "👁 Vision: ON" if active else "👁 Toggle Vision"
                    break

        # ── Callbacks ─────────────────────────────────────────────────

        def _handle_start_stop(self, sender: rumps.MenuItem) -> None:
            if self._pipeline_running:
                if self._on_stop:
                    threading.Thread(target=self._on_stop, daemon=True).start()
            else:
                if self._on_start:
                    threading.Thread(target=self._on_start, daemon=True).start()

        def _handle_vision(self, sender: rumps.MenuItem) -> None:
            if self._on_toggle_vision:
                threading.Thread(target=self._on_toggle_vision, daemon=True).start()

        def _handle_overlay(self, sender: rumps.MenuItem) -> None:
            if self._on_toggle_overlay:
                threading.Thread(target=self._on_toggle_overlay, daemon=True).start()

        def _handle_quit(self, sender: rumps.MenuItem) -> None:
            if self._on_quit:
                self._on_quit()
            rumps.quit_application()


    def create_menubar(
        on_start: Optional[Callable] = None,
        on_stop: Optional[Callable] = None,
        on_toggle_vision: Optional[Callable] = None,
        on_toggle_overlay: Optional[Callable] = None,
        on_quit: Optional[Callable] = None,
    ) -> Optional[EPAgentMenuBar]:
        """Create the menu bar app. Returns None if rumps unavailable."""
        if not RUMPS_AVAILABLE:
            return None
        return EPAgentMenuBar(
            on_start=on_start,
            on_stop=on_stop,
            on_toggle_vision=on_toggle_vision,
            on_toggle_overlay=on_toggle_overlay,
            on_quit=on_quit,
        )

else:
    def create_menubar(*args, **kwargs):
        """Stub when rumps is not installed."""
        logger.warning("Menu bar unavailable — install rumps: pip install rumps")
        return None
