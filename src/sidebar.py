"""
EP Agent Sidebar — Always-on glass side panel with Smart Glow.

A permanent, elegant sidebar anchored to the right edge of the screen.
Features:
- Right 15% of screen width
- Always on top, click-through when idle
- Cmd+Shift+E to slide in/out
- Glassmorphism (blurred background + semi-transparent dark tint)
- Smart Glow border (breathing cyan/purple pulse when listening)
- Rolling transcript
- Vision thumbnails
- Hybrid connectivity indicator (Cloud/Local)

Replaces overlay.py as the primary UI surface.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)

# Accent color map (shared with onboarding)
ACCENT_COLOR_MAP = {
    "cyan": (0, 200, 255),
    "purple": (168, 85, 247),
    "green": (34, 197, 94),
    "amber": (245, 158, 11),
}

try:
    from PyQt6.QtCore import (
        Qt, QTimer, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup,
        pyqtSignal, pyqtSlot, pyqtProperty, QPoint, QSize, QRectF, QRect,
        QByteArray,
    )
    from PyQt6.QtGui import (
        QColor, QFont, QPainter, QLinearGradient, QPen, QRadialGradient,
        QBrush, QPaintEvent, QKeySequence, QShortcut, QPixmap, QImage,
        QPainterPath, QAction, QIcon,
    )
    from PyQt6.QtWidgets import (
        QApplication, QFrame, QGraphicsBlurEffect, QGraphicsDropShadowEffect,
        QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea,
        QSizePolicy, QVBoxLayout, QWidget, QSystemTrayIcon, QMenu,
        QGraphicsOpacityEffect,
    )
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False
    logger.info("PyQt6 not installed — sidebar UI unavailable")


def is_available() -> bool:
    return PYQT6_AVAILABLE


if PYQT6_AVAILABLE:

    # ── Glass Card ────────────────────────────────────────────────────

    class GlassCard(QFrame):
        """Card with frosted glass look."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setStyleSheet("""
                GlassCard {
                    background-color: rgba(18, 20, 30, 180);
                    border: 1px solid rgba(255, 255, 255, 0.06);
                    border-radius: 12px;
                }
            """)
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(16)
            shadow.setColor(QColor(0, 0, 0, 60))
            shadow.setOffset(0, 3)
            self.setGraphicsEffect(shadow)

    # ── Glow Border Widget ────────────────────────────────────────────

    class GlowBorderWidget(QWidget):
        """
        Container widget that draws a glowing animated border.
        The glow pulses (breathing effect) when the agent is listening/active.
        """

        def __init__(self, parent=None, accent_color: str = "cyan"):
            super().__init__(parent)
            self._glow_opacity = 0.0
            r, g, b = ACCENT_COLOR_MAP.get(accent_color, (0, 200, 255))
            self._glow_color = QColor(r, g, b)
            self._breathing = False
            self._breath_phase = 0.0

            # Breathing animation timer
            self._breath_timer = QTimer(self)
            self._breath_timer.setInterval(30)  # ~33fps
            self._breath_timer.timeout.connect(self._tick_breath)

        def set_glow_active(self, active: bool, color: Optional[QColor] = None):
            """Enable/disable the breathing glow."""
            self._breathing = active
            if color:
                self._glow_color = color
            if active:
                self._breath_phase = 0.0
                self._breath_timer.start()
            else:
                self._breath_timer.stop()
                self._glow_opacity = 0.0
                self.update()

        def _tick_breath(self):
            self._breath_phase += 0.06  # Faster pulse for clear visual feedback
            # Smooth sine breathing: 0.4 → 1.0 (more visible minimum)
            self._glow_opacity = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(self._breath_phase))
            self.update()

        def paintEvent(self, event: QPaintEvent):
            super().paintEvent(event)
            if self._glow_opacity <= 0:
                return

            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            w = self.width()
            h = self.height()
            radius = 16

            # Draw glowing border (bold, clearly visible)
            color = QColor(self._glow_color)
            color.setAlphaF(self._glow_opacity * 0.85)

            pen = QPen(color, 3.5)  # Thicker inner border
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            path = QPainterPath()
            path.addRoundedRect(QRectF(3, 3, w - 6, h - 6), radius, radius)
            painter.drawPath(path)

            # Outer soft glow (wider, brighter for clear activation indicator)
            outer_color = QColor(self._glow_color)
            outer_color.setAlphaF(self._glow_opacity * 0.4)
            outer_pen = QPen(outer_color, 10)  # Much wider outer glow
            painter.setPen(outer_pen)
            painter.drawPath(path)

            painter.end()

    # ── Main Sidebar Window ───────────────────────────────────────────

    class EPAgentSidebar(QMainWindow):
        """
        Always-on sidebar anchored to right 15% of screen.
        Glassmorphism design with Smart Glow indicator.
        """

        analysis_received = pyqtSignal(str, float)
        status_changed = pyqtSignal(str)
        transcript_received = pyqtSignal(str, str)  # role, text
        vision_thumbnail_received = pyqtSignal(object)  # QPixmap
        connectivity_changed = pyqtSignal(str)  # "cloud" or "local"

        # Signal to open settings dialog
        settings_requested = pyqtSignal()

        def __init__(self, parent=None, accent_color: str = "cyan", personality: str = "friendly"):
            super().__init__(parent)
            self._visible = True
            self._state = "idle"
            self._connectivity = "local"
            self._drag_pos: Optional[QPoint] = None
            self._accent_color = accent_color
            self._personality = personality
            self._accent_rgb = ACCENT_COLOR_MAP.get(accent_color, (0, 200, 255))

            self._setup_window()
            self._build_ui()
            self._connect_signals()
            self._setup_tray()
            self._setup_shortcuts()

        # ── Window Setup ──────────────────────────────────────────────

        def _setup_window(self):
            self.setWindowTitle("EP Agent")
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                panel_width = int(geo.width() * 0.15)
                panel_width = max(320, min(panel_width, 500))  # Clamp
                self._panel_width = panel_width
                self._screen_geo = geo
                self.setGeometry(
                    geo.x() + geo.width() - panel_width,
                    geo.y(),
                    panel_width,
                    geo.height(),
                )
            else:
                self._panel_width = 380
                self._screen_geo = QRect(0, 0, 1920, 1080)
                self.setGeometry(1540, 0, 380, 1080)

        # ── System Tray ───────────────────────────────────────────────

        def _setup_tray(self):
            """Create system tray icon with context menu (hidden by default).

            The tray icon is available but not shown to avoid clutter in the
            menu bar. Users can toggle sidebar with Cmd+Shift+E instead.
            """
            self._tray = QSystemTrayIcon(self)
            # Create a QIcon from accent-colored pixmap
            pixmap = QPixmap(32, 32)
            r, g, b = self._accent_rgb
            pixmap.fill(QColor(r, g, b))
            self._tray.setIcon(QIcon(pixmap))
            self._tray.setToolTip("EP Agent")

            menu = QMenu()
            show_action = QAction("Show Sidebar", self)
            show_action.triggered.connect(self._slide_in)
            menu.addAction(show_action)

            hide_action = QAction("Hide Sidebar", self)
            hide_action.triggered.connect(self._slide_out)
            menu.addAction(hide_action)

            menu.addSeparator()

            quit_action = QAction("Quit EP Agent", self)
            quit_action.triggered.connect(QApplication.quit)
            menu.addAction(quit_action)

            self._tray.setContextMenu(menu)
            self._tray.activated.connect(self._tray_activated)
            # Don't show tray icon — no yellow/colored icon in menu bar
            # self._tray.show()

        def _tray_activated(self, reason):
            if reason == QSystemTrayIcon.ActivationReason.Trigger:
                self._toggle_visibility()

        # ── Keyboard Shortcut ─────────────────────────────────────────

        def _setup_shortcuts(self):
            """Cmd+Shift+E to toggle sidebar."""
            shortcut = QShortcut(QKeySequence("Meta+Shift+E"), self)
            shortcut.activated.connect(self._toggle_visibility)

        def _toggle_visibility(self):
            if self._visible:
                self._slide_out()
            else:
                self._slide_in()

        def _slide_out(self):
            """Slide panel off screen to the right."""
            self._visible = False
            end_x = self._screen_geo.x() + self._screen_geo.width() + 10
            self._animate_x(self.x(), end_x)

        def _slide_in(self):
            """Slide panel back to its anchored position."""
            self._visible = True
            target_x = self._screen_geo.x() + self._screen_geo.width() - self._panel_width
            self._animate_x(self.x(), target_x)
            self.show()
            self.raise_()

        def _animate_x(self, start_x: int, end_x: int):
            """Smooth horizontal slide animation."""
            self._slide_anim = QPropertyAnimation(self, b"pos")
            self._slide_anim.setDuration(250)
            self._slide_anim.setStartValue(QPoint(start_x, self.y()))
            self._slide_anim.setEndValue(QPoint(end_x, self.y()))
            self._slide_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
            self._slide_anim.start()

        # ── UI Build ──────────────────────────────────────────────────

        def _build_ui(self):
            # Glow border container
            self._glow_border = GlowBorderWidget(accent_color=self._accent_color)
            self.setCentralWidget(self._glow_border)

            # Background panel inside glow border
            panel = QWidget(self._glow_border)
            panel.setStyleSheet("""
                background-color: rgba(10, 12, 18, 220);
                border-radius: 16px;
            """)

            # Use a layout for the glow border
            glow_layout = QVBoxLayout(self._glow_border)
            glow_layout.setContentsMargins(4, 4, 4, 4)
            glow_layout.addWidget(panel)

            # Main layout inside panel
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(16, 14, 16, 14)
            layout.setSpacing(10)

            # ── Header ────────────────────────────────────────────────
            header = self._build_header()
            layout.addWidget(header)

            # ── Transcript (rolling chat) ─────────────────────────────
            self._scroll = QScrollArea()
            self._scroll.setWidgetResizable(True)
            self._scroll.setStyleSheet("""
                QScrollArea {
                    border: none;
                    background: transparent;
                }
                QScrollBar:vertical {
                    width: 4px;
                    background: transparent;
                }
                QScrollBar::handle:vertical {
                    background: rgba(255,255,255,0.08);
                    border-radius: 2px;
                    min-height: 30px;
                }
                QScrollBar::handle:vertical:hover {
                    background: rgba(0,200,255,0.3);
                }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                    height: 0;
                }
            """)

            self._chat_container = QWidget()
            self._chat_container.setStyleSheet("background: transparent;")
            self._chat_layout = QVBoxLayout(self._chat_container)
            self._chat_layout.setContentsMargins(0, 0, 0, 0)
            self._chat_layout.setSpacing(8)
            self._chat_layout.addStretch()

            self._scroll.setWidget(self._chat_container)
            layout.addWidget(self._scroll, 1)

            # ── Vision Thumbnail Area ─────────────────────────────────
            self._vision_card = GlassCard()
            self._vision_card.setFixedHeight(100)
            self._vision_card.setVisible(False)
            vision_layout = QHBoxLayout(self._vision_card)
            vision_layout.setContentsMargins(10, 8, 10, 8)

            self._vision_thumb = QLabel()
            self._vision_thumb.setFixedSize(120, 80)
            self._vision_thumb.setStyleSheet("""
                background: rgba(0,0,0,0.3);
                border-radius: 6px;
                border: 1px solid rgba(0,200,255,0.2);
            """)
            self._vision_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._vision_thumb.setScaledContents(True)
            vision_layout.addWidget(self._vision_thumb)

            vision_info = QVBoxLayout()
            vision_info.setSpacing(4)
            self._vision_label = QLabel("Screen Analysis")
            self._vision_label.setFont(QFont(".AppleSystemUIFont", 10, QFont.Weight.DemiBold))
            self._vision_label.setStyleSheet("color: rgba(0,200,255,0.8); background: transparent;")
            vision_info.addWidget(self._vision_label)

            self._vision_desc = QLabel("Analyzing...")
            self._vision_desc.setWordWrap(True)
            self._vision_desc.setFont(QFont(".AppleSystemUIFont", 9))
            self._vision_desc.setStyleSheet("color: rgba(255,255,255,0.5); background: transparent;")
            vision_info.addWidget(self._vision_desc)
            vision_info.addStretch()
            vision_layout.addLayout(vision_info, 1)

            layout.addWidget(self._vision_card)

            # ── Footer (connectivity + personality indicator) ──────────
            footer = QWidget()
            footer.setStyleSheet("background: transparent;")
            footer_layout = QHBoxLayout(footer)
            footer_layout.setContentsMargins(8, 4, 8, 4)

            self._connectivity_dot = QLabel("●")
            self._connectivity_dot.setFont(QFont("", 8))
            self._connectivity_dot.setStyleSheet("color: #00dc78; background: transparent;")
            footer_layout.addWidget(self._connectivity_dot)

            self._connectivity_label = QLabel("Local Mode (Private)")
            self._connectivity_label.setFont(QFont(".AppleSystemUIFont", 9))
            self._connectivity_label.setStyleSheet("color: rgba(255,255,255,0.3); background: transparent;")
            footer_layout.addWidget(self._connectivity_label)

            footer_layout.addStretch()

            # Personality indicator
            r, g, b = self._accent_rgb
            self._personality_label = QLabel(self._personality.capitalize())
            self._personality_label.setFont(QFont(".AppleSystemUIFont", 9))
            self._personality_label.setStyleSheet(
                f"color: rgba({r},{g},{b},0.5); background: transparent; padding-right: 8px;"
            )
            footer_layout.addWidget(self._personality_label)

            self._status_label = QLabel("Idle")
            self._status_label.setFont(QFont(".AppleSystemUIFont", 9))
            self._status_label.setStyleSheet("color: rgba(255,255,255,0.25); background: transparent;")
            footer_layout.addWidget(self._status_label)

            layout.addWidget(footer)

        def _build_header(self) -> QWidget:
            """Build the header card with title, settings button, and controls."""
            card = GlassCard()
            layout = QHBoxLayout(card)
            layout.setContentsMargins(14, 10, 14, 10)

            # EP Agent title (use accent color)
            r, g, b = self._accent_rgb
            title = QLabel("EP")
            title.setFont(QFont(".AppleSystemUIFont", 20, QFont.Weight.Bold))
            title.setStyleSheet(f"color: rgb({r},{g},{b}); background: transparent; letter-spacing: 2px;")
            layout.addWidget(title)

            subtitle = QLabel("AGENT")
            subtitle.setFont(QFont(".AppleSystemUIFont", 12, QFont.Weight.Light))
            subtitle.setStyleSheet("color: rgba(255,255,255,0.4); background: transparent; padding-top: 6px;")
            layout.addWidget(subtitle)

            layout.addStretch()

            # Settings button
            settings_btn = QPushButton("⚙️")
            settings_btn.setFixedSize(28, 28)
            settings_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255,255,255,0.04);
                    color: rgba(255,255,255,0.5);
                    border: none;
                    border-radius: 14px;
                    font-size: 14px;
                }
                QPushButton:hover {
                    background: rgba(255,255,255,0.1);
                    color: rgba(255,255,255,0.8);
                }
            """)
            settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            settings_btn.setToolTip("Settings")
            settings_btn.clicked.connect(self._open_settings)
            layout.addWidget(settings_btn)

            # Minimize button
            min_btn = QPushButton("─")
            min_btn.setFixedSize(24, 24)
            min_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255,255,255,0.04);
                    color: rgba(255,255,255,0.3);
                    border: none;
                    border-radius: 12px;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background: rgba(255,255,255,0.1);
                    color: rgba(255,255,255,0.6);
                }
            """)
            min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            min_btn.clicked.connect(self._slide_out)
            layout.addWidget(min_btn)

            return card

        def _open_settings(self):
            """Emit signal to open settings/onboarding dialog."""
            self.settings_requested.emit()

        # ── Signals ───────────────────────────────────────────────────

        def _connect_signals(self):
            self.analysis_received.connect(self._on_analysis)
            self.status_changed.connect(self._on_status_changed)
            self.transcript_received.connect(self._on_transcript)
            self.vision_thumbnail_received.connect(self._on_vision_thumbnail)
            self.connectivity_changed.connect(self._on_connectivity_changed)

        # ── Slots ────────────────────────────────────────────────────

        @pyqtSlot(str, float)
        def _on_analysis(self, text: str, elapsed_ms: float):
            """Show analysis result in the transcript."""
            self._add_message("agent", f"🔍 {text}", elapsed_ms=elapsed_ms)

        @pyqtSlot(str)
        def _on_status_changed(self, status: str):
            self._state = status

            # Smart Glow: pulse border when listening/active
            # Listening uses the user's accent color for clear visual feedback
            r, g, b = self._accent_rgb
            if status in ("listening",):
                self._glow_border.set_glow_active(True, QColor(r, g, b))  # Accent color
            elif status in ("analyzing",):
                self._glow_border.set_glow_active(True, QColor(140, 80, 255))  # Purple
            elif status in ("processing",):
                self._glow_border.set_glow_active(True, QColor(255, 180, 0))  # Amber
            elif status == "speaking":
                self._glow_border.set_glow_active(True, QColor(0, 220, 120))  # Green
            else:
                self._glow_border.set_glow_active(False)

            # Status label
            labels = {
                "idle": "Idle",
                "listening": "Listening…",
                "analyzing": "Analyzing…",
                "processing": "Processing…",
                "speaking": "Speaking…",
                "error": "Error",
            }
            colors = {
                "idle": "rgba(255,255,255,0.25)",
                "listening": "#00c8ff",
                "analyzing": "#8c50ff",
                "processing": "#ffb400",
                "speaking": "#00dc78",
                "error": "#ff4444",
            }
            self._status_label.setText(labels.get(status, status))
            self._status_label.setStyleSheet(
                f"color: {colors.get(status, 'rgba(255,255,255,0.25)')}; background: transparent;"
            )

        @pyqtSlot(str, str)
        def _on_transcript(self, role: str, text: str):
            """Add a message to the rolling transcript."""
            self._add_message(role, text)

        @pyqtSlot(object)
        def _on_vision_thumbnail(self, pixmap):
            """Show a vision thumbnail in the sidebar."""
            if pixmap and not pixmap.isNull():
                self._vision_thumb.setPixmap(pixmap)
                self._vision_card.setVisible(True)
                self._vision_desc.setText("Last analyzed region")

        @pyqtSlot(str)
        def _on_connectivity_changed(self, mode: str):
            """Update the connectivity indicator."""
            self._connectivity = mode
            if mode == "cloud":
                self._connectivity_dot.setStyleSheet("color: #00c8ff; background: transparent;")
                self._connectivity_label.setText("Cloud Connected")
            else:
                self._connectivity_dot.setStyleSheet("color: #00dc78; background: transparent;")
                self._connectivity_label.setText("Local Mode (Private)")

        # ── Transcript Management ─────────────────────────────────────

        def _add_message(self, role: str, text: str, elapsed_ms: float = 0):
            """Add a bubble to the rolling transcript."""
            bubble = QFrame()
            bubble_layout = QVBoxLayout(bubble)
            bubble_layout.setContentsMargins(10, 8, 10, 8)
            bubble_layout.setSpacing(2)

            if role == "user":
                bubble.setStyleSheet("""
                    QFrame {
                        background: rgba(0, 200, 255, 0.08);
                        border: 1px solid rgba(0, 200, 255, 0.15);
                        border-radius: 10px;
                    }
                """)
                prefix = "You"
                prefix_color = "#00c8ff"
            else:
                bubble.setStyleSheet("""
                    QFrame {
                        background: rgba(140, 80, 255, 0.06);
                        border: 1px solid rgba(140, 80, 255, 0.12);
                        border-radius: 10px;
                    }
                """)
                prefix = "EP"
                prefix_color = "#8c50ff"

            # Header
            header = QLabel(f"{prefix}  ·  {datetime.now().strftime('%H:%M')}")
            header.setFont(QFont(".AppleSystemUIFont", 9))
            header.setStyleSheet(f"color: {prefix_color}; background: transparent; opacity: 0.6;")
            bubble_layout.addWidget(header)

            # Content
            content = QLabel(text)
            content.setWordWrap(True)
            content.setFont(QFont(".AppleSystemUIFont", 11))
            content.setStyleSheet("color: rgba(255,255,255,0.85); background: transparent;")
            content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            bubble_layout.addWidget(content)

            # Timing (for agent messages)
            if elapsed_ms > 0 and role != "user":
                timing_text = f"{elapsed_ms/1000:.1f}s" if elapsed_ms > 1000 else f"{int(elapsed_ms)}ms"
                timing = QLabel(f"⚡ {timing_text}")
                timing.setFont(QFont("Menlo", 8))
                timing.setStyleSheet("color: rgba(0,200,255,0.4); background: transparent;")
                bubble_layout.addWidget(timing)

            # Insert before the stretch
            count = self._chat_layout.count()
            self._chat_layout.insertWidget(count - 1, bubble)

            # Cap at 50 messages
            while self._chat_layout.count() > 52:
                item = self._chat_layout.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()

            # Auto-scroll
            QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(
                self._scroll.verticalScrollBar().maximum()
            ))

        # ── Public API (thread-safe via signals) ──────────────────────

        def push_analysis(self, text: str, elapsed_ms: float = 0):
            """Thread-safe: push analysis result."""
            self.analysis_received.emit(text, elapsed_ms)

        def set_status(self, status: str):
            """Thread-safe: update state."""
            self.status_changed.emit(status)

        def push_transcript(self, role: str, text: str):
            """Thread-safe: add a transcript message."""
            self.transcript_received.emit(role, text)

        def set_vision_thumbnail(self, pixmap):
            """Thread-safe: set the vision thumbnail."""
            self.vision_thumbnail_received.emit(pixmap)

        def set_connectivity(self, mode: str):
            """Thread-safe: update connectivity ('cloud' or 'local')."""
            self.connectivity_changed.emit(mode)

        # ── Click-through when idle ───────────────────────────────────

        def _update_input_transparency(self):
            """Make window click-through when idle, interactive otherwise."""
            if self._state == "idle":
                self.setWindowFlags(
                    self.windowFlags() | Qt.WindowType.WindowTransparentForInput
                )
            else:
                self.setWindowFlags(
                    self.windowFlags() & ~Qt.WindowType.WindowTransparentForInput
                )
            self.show()

        # ── Drag Support ──────────────────────────────────────────────

        def mousePressEvent(self, event):
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()

        def mouseMoveEvent(self, event):
            if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                event.accept()

        def mouseReleaseEvent(self, event):
            self._drag_pos = None

        # ── Profile Application ───────────────────────────────────────

        def apply_accent_color(self, color_name: str):
            """Update accent color across the sidebar."""
            self._accent_color = color_name
            self._accent_rgb = ACCENT_COLOR_MAP.get(color_name, (0, 200, 255))
            r, g, b = self._accent_rgb
            self._glow_border._glow_color = QColor(r, g, b)
            self._personality_label.setStyleSheet(
                f"color: rgba({r},{g},{b},0.5); background: transparent; padding-right: 8px;"
            )

        def apply_personality(self, personality: str):
            """Update personality label."""
            self._personality = personality
            self._personality_label.setText(personality.capitalize())


    def create_sidebar(
        accent_color: str = "cyan",
        personality: str = "friendly",
    ) -> Optional["EPAgentSidebar"]:
        """Create the EP Agent sidebar. Requires a running QApplication."""
        if not PYQT6_AVAILABLE:
            logger.error("PyQt6 not installed")
            return None
        return EPAgentSidebar(accent_color=accent_color, personality=personality)

else:
    def create_sidebar(*args, **kwargs):
        logger.error("PyQt6 not installed — sidebar unavailable")
        return None
