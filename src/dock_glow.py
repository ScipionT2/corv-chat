"""
Dock Glow Indicator — visual feedback bar above the macOS Dock.

A thin, frameless, translucent window that sits at the very bottom of
the screen and glows/pulses when Nova is actively listening or
processing. Replaces the system microphone icon with a sleek visual cue.

States:
- idle:       hidden (fully transparent)
- listening:  cyan pulse animation
- processing: amber steady glow
- speaking:   green steady glow
- error:      red flash
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from PyQt6.QtCore import (
        Qt, QTimer, QPropertyAnimation, QEasingCurve,
        pyqtSignal, pyqtSlot, QRect,
    )
    from PyQt6.QtGui import (
        QColor, QPainter, QLinearGradient, QRadialGradient, QPaintEvent,
    )
    from PyQt6.QtWidgets import QApplication, QWidget
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False
    logger.info("PyQt6 not installed — dock glow unavailable")


if PYQT6_AVAILABLE:

    class DockGlow(QWidget):
        """Glowing bar indicator above the macOS Dock.

        Thread-safe: call set_state() from any thread; actual Qt
        operations are marshalled to the main thread via signal.
        """

        state_changed = pyqtSignal(str)
        _state_request = pyqtSignal(str)  # internal: cross-thread dispatch

        # ── Color Schemes ─────────────────────────────────────────────
        COLORS = {
            "idle":       (QColor(0, 0, 0, 0), QColor(0, 0, 0, 0)),
            "listening":  (QColor(0, 200, 255, 180), QColor(0, 120, 255, 60)),
            "processing": (QColor(255, 180, 0, 160), QColor(255, 120, 0, 40)),
            "speaking":   (QColor(0, 255, 120, 160), QColor(0, 180, 80, 40)),
            "error":      (QColor(255, 50, 50, 200), QColor(255, 0, 0, 60)),
        }

        BAR_HEIGHT = 4          # pixels tall
        GLOW_HEIGHT = 40        # total window height (bar + gradient fade)
        PULSE_MS = 1200         # pulse cycle duration
        FADE_IN_MS = 200
        FADE_OUT_MS = 400

        def __init__(self, parent=None):
            super().__init__(parent)
            self._state = "idle"
            self._opacity = 0.0
            self._pulse_phase = 0.0
            self._target_opacity = 0.0

            self._setup_window()
            self._setup_animation()

        def _setup_window(self):
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowTransparentForInput
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

            # Position at the very bottom of the screen
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                full_geo = screen.geometry()
                # Sit just above the dock area
                dock_top = geo.y() + geo.height()
                self.setGeometry(
                    geo.x(),
                    dock_top - self.GLOW_HEIGHT,
                    geo.width(),
                    self.GLOW_HEIGHT,
                )

        def _setup_animation(self):
            # Pulse timer for listening state
            self._pulse_timer = QTimer(self)
            self._pulse_timer.setInterval(30)  # ~33fps
            self._pulse_timer.timeout.connect(self._tick_pulse)

            # Fade timer
            self._fade_timer = QTimer(self)
            self._fade_timer.setInterval(16)  # ~60fps
            self._fade_timer.timeout.connect(self._tick_fade)

            # Cross-thread signal → main-thread slot
            self._state_request.connect(self._apply_state, Qt.ConnectionType.QueuedConnection)

        # ── Public API ────────────────────────────────────────────────

        def set_state(self, state: str):
            """Thread-safe state update. Safe to call from any thread."""
            # Dispatch to main thread via queued signal
            self._state_request.emit(state)

        @pyqtSlot(str)
        def _apply_state(self, state: str):
            """Apply state change on the main thread (slot)."""
            if state == self._state:
                return
            self._state = state
            self.state_changed.emit(state)

            if state == "idle":
                self._target_opacity = 0.0
                self._pulse_timer.stop()
                self._fade_timer.start()
            elif state == "listening":
                self._target_opacity = 1.0
                self._pulse_phase = 0.0
                self._fade_timer.start()
                self._pulse_timer.start()
                self.show()
                self.raise_()
            elif state in ("processing", "speaking"):
                self._target_opacity = 1.0
                self._pulse_timer.stop()
                self._fade_timer.start()
                self.show()
                self.raise_()
            elif state == "error":
                self._target_opacity = 1.0
                self._pulse_timer.stop()
                self._fade_timer.start()
                self.show()
                self.raise_()
                # Auto-hide after 2s
                QTimer.singleShot(2000, lambda: self._state_request.emit("idle"))

        @pyqtSlot()
        def _tick_pulse(self):
            """Animate the pulse for 'listening' state."""
            import math
            self._pulse_phase += 0.05
            # Sine wave between 0.4 and 1.0
            self._opacity = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(self._pulse_phase * 2))
            self.update()

        @pyqtSlot()
        def _tick_fade(self):
            """Smooth fade in/out."""
            step = 0.08 if self._target_opacity > self._opacity else 0.06
            diff = self._target_opacity - self._opacity

            if abs(diff) < 0.01:
                self._opacity = self._target_opacity
                self._fade_timer.stop()
                if self._opacity <= 0:
                    self.hide()
                return

            self._opacity += step if diff > 0 else -step
            self._opacity = max(0.0, min(1.0, self._opacity))
            self.update()

        # ── Paint ─────────────────────────────────────────────────────

        def paintEvent(self, event: QPaintEvent):
            if self._opacity <= 0:
                return

            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            w = self.width()
            h = self.height()
            colors = self.COLORS.get(self._state, self.COLORS["idle"])
            core_color, edge_color = colors

            # Apply current opacity
            core = QColor(core_color)
            core.setAlphaF(core_color.alphaF() * self._opacity)
            edge = QColor(edge_color)
            edge.setAlphaF(edge_color.alphaF() * self._opacity)

            # Bottom bar (solid glow line)
            bar_y = h - self.BAR_HEIGHT
            painter.fillRect(0, bar_y, w, self.BAR_HEIGHT, core)

            # Upward gradient fade
            grad = QLinearGradient(0, bar_y, 0, 0)
            grad.setColorAt(0.0, edge)
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(0, 0, w, bar_y, grad)

            # Center highlight (brighter in the middle)
            center_x = w / 2
            radial = QRadialGradient(center_x, h, w * 0.4)
            highlight = QColor(core)
            highlight.setAlphaF(min(1.0, core.alphaF() * 0.5))
            radial.setColorAt(0.0, highlight)
            radial.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(0, 0, w, h, radial)

            painter.end()


    def create_dock_glow() -> Optional["DockGlow"]:
        """Create a DockGlow widget. Requires a running QApplication."""
        if not PYQT6_AVAILABLE:
            return None
        glow = DockGlow()
        return glow

else:
    def create_dock_glow():
        return None
