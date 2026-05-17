"""
Nova Side-Panel Overlay — on-demand glass-effect GUI.

A polished, dark-glass side panel pinned to the right edge of the screen.
Displays real-time vision analysis, voice status, and suggestions.
Only rendered when explicitly toggled (saves GPU draw calls).

Design:
- Frosted dark glass aesthetic
- Smooth animations and transitions
- Cards with subtle borders and depth
- Status ring with state-based colors
- Shown on demand via menu bar toggle
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from PyQt6.QtCore import (
        Qt, QTimer, QPropertyAnimation, QEasingCurve,
        pyqtSignal, pyqtSlot, QPoint, QSize, QRectF,
    )
    from PyQt6.QtGui import (
        QColor, QFont, QPainter, QLinearGradient, QPen,
        QBrush, QPaintEvent, QFontDatabase,
    )
    from PyQt6.QtWidgets import (
        QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
        QLabel, QMainWindow, QPushButton, QScrollArea, QSizePolicy,
        QVBoxLayout, QWidget,
    )
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False
    logger.info("PyQt6 not installed — overlay UI unavailable")


def is_available() -> bool:
    return PYQT6_AVAILABLE


if PYQT6_AVAILABLE:

    # ── Styled Card Widget ────────────────────────────────────────────

    class GlassCard(QFrame):
        """A card with frosted glass appearance."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setStyleSheet("""
                GlassCard {
                    background-color: rgba(22, 24, 35, 200);
                    border: 1px solid rgba(255, 255, 255, 0.06);
                    border-radius: 12px;
                }
            """)
            # Subtle shadow
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(20)
            shadow.setColor(QColor(0, 0, 0, 80))
            shadow.setOffset(0, 4)
            self.setGraphicsEffect(shadow)

    # ── Status Ring ───────────────────────────────────────────────────

    class StatusRing(QWidget):
        """Animated ring that shows Nova state."""

        STATE_COLORS = {
            "idle": QColor(100, 100, 120),
            "listening": QColor(0, 200, 255),
            "analyzing": QColor(0, 220, 120),
            "processing": QColor(255, 180, 0),
            "speaking": QColor(120, 80, 255),
            "error": QColor(255, 60, 60),
        }

        def __init__(self, size: int = 48, parent=None):
            super().__init__(parent)
            self._size = size
            self._state = "idle"
            self._angle = 0
            self.setFixedSize(size, size)

            self._timer = QTimer(self)
            self._timer.setInterval(30)
            self._timer.timeout.connect(self._tick)

        def set_state(self, state: str):
            self._state = state
            if state in ("listening", "analyzing", "processing"):
                self._timer.start()
            else:
                self._timer.stop()
            self.update()

        def _tick(self):
            self._angle = (self._angle + 4) % 360
            self.update()

        def paintEvent(self, event: QPaintEvent):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            color = self.STATE_COLORS.get(self._state, self.STATE_COLORS["idle"])
            s = self._size
            margin = 4
            rect = QRectF(margin, margin, s - 2 * margin, s - 2 * margin)

            # Outer ring
            pen = QPen(color, 3)
            painter.setPen(pen)
            painter.drawEllipse(rect)

            # Spinning arc for active states
            if self._state in ("listening", "analyzing", "processing"):
                bright = QColor(color)
                bright.setAlpha(255)
                pen2 = QPen(bright, 3)
                painter.setPen(pen2)
                painter.drawArc(rect, self._angle * 16, 90 * 16)

            # Center dot
            dot_size = 8
            dot_rect = QRectF(
                s / 2 - dot_size / 2,
                s / 2 - dot_size / 2,
                dot_size,
                dot_size,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(dot_rect)

            painter.end()

    # ── Main Overlay Window ───────────────────────────────────────────

    class NovaOverlay(QMainWindow):
        """Side-panel overlay with frosted glass design (Nova)."""

        analysis_received = pyqtSignal(str, float)
        status_changed = pyqtSignal(str)

        def __init__(
            self,
            width: int = 380,
            opacity: float = 0.95,
            on_toggle=None,
            parent=None,
        ):
            super().__init__(parent)
            self._width = width
            self._opacity = opacity
            self._on_toggle = on_toggle
            self._drag_pos: Optional[QPoint] = None
            self._analysis_active = False
            self._state = "idle"

            self._setup_window()
            self._build_ui()
            self._connect_signals()

        # ── Window Setup ──────────────────────────────────────────────

        def _setup_window(self):
            self.setWindowTitle("Nova")
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setWindowOpacity(self._opacity)

            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                self.setGeometry(
                    geo.width() - self._width - 12,
                    50,
                    self._width,
                    geo.height() - 100,
                )

        # ── UI ────────────────────────────────────────────────────────

        def _build_ui(self):
            # Main container with background
            central = QWidget()
            central.setStyleSheet("""
                background-color: rgba(12, 13, 20, 230);
                border-radius: 16px;
                border: 1px solid rgba(255, 255, 255, 0.04);
            """)
            self.setCentralWidget(central)

            # Drop shadow on the whole panel
            shadow = QGraphicsDropShadowEffect(central)
            shadow.setBlurRadius(40)
            shadow.setColor(QColor(0, 140, 255, 50))
            shadow.setOffset(0, 0)
            central.setGraphicsEffect(shadow)

            layout = QVBoxLayout(central)
            layout.setContentsMargins(20, 16, 20, 16)
            layout.setSpacing(12)

            # ── Header ────────────────────────────────────────────────
            header_card = GlassCard()
            header_layout = QHBoxLayout(header_card)
            header_layout.setContentsMargins(16, 12, 16, 12)

            # Status ring
            self._status_ring = StatusRing(size=44)
            header_layout.addWidget(self._status_ring)

            # Title + status text
            title_col = QVBoxLayout()
            title_col.setSpacing(2)

            title = QLabel("EP AGENT")
            title.setFont(QFont(".AppleSystemUIFont", 18, QFont.Weight.Bold))
            title.setStyleSheet("color: #00c8ff; background: transparent; letter-spacing: 3px;")
            title_col.addWidget(title)

            self._status_label = QLabel("Idle")
            self._status_label.setFont(QFont(".AppleSystemUIFont", 11))
            self._status_label.setStyleSheet("color: rgba(255,255,255,0.4); background: transparent;")
            title_col.addWidget(self._status_label)

            header_layout.addLayout(title_col, 1)

            # Close button
            close_btn = QPushButton("✕")
            close_btn.setFixedSize(28, 28)
            close_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255,255,255,0.05);
                    color: rgba(255,255,255,0.3);
                    border: none;
                    border-radius: 14px;
                    font-size: 14px;
                }
                QPushButton:hover {
                    background: rgba(255,60,60,0.3);
                    color: #ff6666;
                }
            """)
            close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            close_btn.clicked.connect(self.hide)
            header_layout.addWidget(close_btn)

            layout.addWidget(header_card)

            # ── Toggle Button ─────────────────────────────────────────
            self._toggle_btn = QPushButton("⏵  Start Analysis")
            self._toggle_btn.setFont(QFont(".AppleSystemUIFont", 13, QFont.Weight.DemiBold))
            self._toggle_btn.setFixedHeight(44)
            self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(False)
            self._toggle_btn.clicked.connect(self._on_toggle_clicked)
            layout.addWidget(self._toggle_btn)

            # ── Results Scroll Area ───────────────────────────────────
            self._scroll = QScrollArea()
            self._scroll.setWidgetResizable(True)
            self._scroll.setStyleSheet("""
                QScrollArea {
                    border: none;
                    background: transparent;
                }
                QScrollBar:vertical {
                    width: 5px;
                    background: transparent;
                    margin: 0;
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

            self._output_container = QWidget()
            self._output_container.setStyleSheet("background: transparent;")
            self._output_layout = QVBoxLayout(self._output_container)
            self._output_layout.setContentsMargins(0, 0, 0, 0)
            self._output_layout.setSpacing(10)

            # Welcome card
            self._add_welcome_card()
            self._output_layout.addStretch()

            self._scroll.setWidget(self._output_container)
            layout.addWidget(self._scroll, 1)

            # ── Footer ────────────────────────────────────────────────
            footer = QLabel("100% Local  ·  Zero Cloud  ·  Full Privacy")
            footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
            footer.setFont(QFont(".AppleSystemUIFont", 9))
            footer.setStyleSheet("color: rgba(255,255,255,0.15); background: transparent; padding: 4px;")
            layout.addWidget(footer)

        def _add_welcome_card(self):
            """Add the initial welcome/instructions card."""
            card = GlassCard()
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 14, 16, 14)
            card_layout.setSpacing(8)

            icon = QLabel("⚡")
            icon.setFont(QFont("", 28))
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon.setStyleSheet("background: transparent;")
            card_layout.addWidget(icon)

            welcome = QLabel("Ready to analyze")
            welcome.setFont(QFont(".AppleSystemUIFont", 15, QFont.Weight.DemiBold))
            welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
            welcome.setStyleSheet("color: rgba(255,255,255,0.8); background: transparent;")
            card_layout.addWidget(welcome)

            hint = QLabel(
                'Say the wake word or click\n'
                'Start Analysis for continuous monitoring.'
            )
            hint.setFont(QFont(".AppleSystemUIFont", 11))
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setWordWrap(True)
            hint.setStyleSheet("color: rgba(255,255,255,0.3); background: transparent; line-height: 1.5;")
            card_layout.addWidget(hint)

            self._welcome_card = card
            self._output_layout.addWidget(card)

        def _style_toggle(self, active: bool):
            if active:
                self._toggle_btn.setText("⏸  Stop Analysis")
                self._toggle_btn.setStyleSheet("""
                    QPushButton {
                        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                            stop:0 rgba(0,200,255,0.25), stop:1 rgba(0,140,255,0.15));
                        color: #00c8ff;
                        border: 1px solid rgba(0,200,255,0.3);
                        border-radius: 10px;
                        padding: 0 20px;
                    }
                    QPushButton:hover {
                        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                            stop:0 rgba(0,200,255,0.35), stop:1 rgba(0,140,255,0.25));
                    }
                """)
            else:
                self._toggle_btn.setText("⏵  Start Analysis")
                self._toggle_btn.setStyleSheet("""
                    QPushButton {
                        background: rgba(255,255,255,0.04);
                        color: rgba(255,255,255,0.5);
                        border: 1px solid rgba(255,255,255,0.06);
                        border-radius: 10px;
                        padding: 0 20px;
                    }
                    QPushButton:hover {
                        background: rgba(255,255,255,0.08);
                        color: rgba(255,255,255,0.7);
                        border: 1px solid rgba(0,200,255,0.2);
                    }
                """)

        # ── Signals ───────────────────────────────────────────────────

        def _connect_signals(self):
            self.analysis_received.connect(self._add_analysis)
            self.status_changed.connect(self._update_status)

        # ── Slots ────────────────────────────────────────────────────

        @pyqtSlot(str, float)
        def _add_analysis(self, text: str, elapsed_ms: float):
            # Remove welcome card on first result
            if hasattr(self, "_welcome_card") and self._welcome_card:
                self._welcome_card.setParent(None)
                self._welcome_card.deleteLater()
                self._welcome_card = None

            now = datetime.now().strftime("%H:%M:%S")
            if elapsed_ms > 1000:
                timing = f"{elapsed_ms / 1000:.1f}s"
            else:
                timing = f"{int(elapsed_ms)}ms"

            card = GlassCard()
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(6)

            # Header row: time + elapsed
            meta_row = QHBoxLayout()
            time_label = QLabel(f"🕐 {now}")
            time_label.setFont(QFont("Menlo", 10))
            time_label.setStyleSheet("color: rgba(255,255,255,0.25); background: transparent;")
            meta_row.addWidget(time_label)

            meta_row.addStretch()

            speed = QLabel(f"⚡ {timing}")
            speed.setFont(QFont("Menlo", 10))
            speed.setStyleSheet("color: rgba(0,200,255,0.5); background: transparent;")
            meta_row.addWidget(speed)

            card_layout.addLayout(meta_row)

            # Analysis text
            content = QLabel(text)
            content.setWordWrap(True)
            content.setFont(QFont(".AppleSystemUIFont", 12))
            content.setStyleSheet("""
                color: rgba(255,255,255,0.85);
                background: transparent;
                line-height: 1.5;
            """)
            card_layout.addWidget(content)

            # Insert above stretch
            count = self._output_layout.count()
            self._output_layout.insertWidget(count - 1, card)

            # Keep last 15 cards
            while self._output_layout.count() > 17:  # 15 cards + stretch + maybe welcome
                item = self._output_layout.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()

            # Scroll to bottom
            QTimer.singleShot(80, lambda: self._scroll.verticalScrollBar().setValue(
                self._scroll.verticalScrollBar().maximum()
            ))

        @pyqtSlot(str)
        def _update_status(self, status: str):
            self._state = status
            self._status_ring.set_state(status)

            labels = {
                "idle": "Idle",
                "listening": "Listening…",
                "analyzing": "Analyzing screen…",
                "processing": "Processing…",
                "speaking": "Speaking…",
                "error": "Error",
            }
            colors = {
                "idle": "rgba(255,255,255,0.3)",
                "listening": "#00c8ff",
                "analyzing": "#00dc78",
                "processing": "#ffb400",
                "speaking": "#7850ff",
                "error": "#ff4444",
            }

            self._status_label.setText(labels.get(status, status.capitalize()))
            self._status_label.setStyleSheet(
                f"color: {colors.get(status, 'rgba(255,255,255,0.3)')}; background: transparent;"
            )

            is_active = status == "analyzing"
            self._style_toggle(is_active)
            self._analysis_active = is_active

        # ── Actions ──────────────────────────────────────────────────

        def _on_toggle_clicked(self):
            if self._on_toggle:
                self._on_toggle()

        def push_analysis(self, text: str, elapsed_ms: float = 0):
            """Thread-safe: push analysis from any thread."""
            self.analysis_received.emit(text, elapsed_ms)

        def set_status(self, status: str):
            """Thread-safe: update status from any thread."""
            self.status_changed.emit(status)

        # ── Drag ─────────────────────────────────────────────────────

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


    def create_overlay(
        on_toggle=None,
        width: int = 380,
        opacity: float = 0.95,
    ) -> Optional["NovaOverlay"]:
        if not PYQT6_AVAILABLE:
            logger.error("PyQt6 not installed")
            return None
        return NovaOverlay(width=width, opacity=opacity, on_toggle=on_toggle)

else:
    def create_overlay(*args, **kwargs):
        logger.error("PyQt6 not installed — overlay unavailable")
        return None
