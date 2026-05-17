"""
Nova Control Center — launcher and dashboard window.

A compact PyQt6 window (400x500) that acts as the main control panel:
- Start/Stop the Nova pipeline + sidebar
- Monitor Ollama, models, and voice status
- Quick settings (theme, accent, voice)
- Launch sidebar on demand
- Menu bar tray icon with status indicator
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QSize
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QAction
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSystemTrayIcon,
    QMenu,
    QVBoxLayout,
    QWidget,
    QCheckBox,
    QFrame,
)

import config

logger = logging.getLogger(__name__)

# ─── Version ───────────────────────────────────────────────────────────────────
__version__ = "1.0.0"

# ─── Accent color palette ─────────────────────────────────────────────────────
ACCENT_COLORS = {
    "cyan": "#00BCD4",
    "purple": "#9C27B0",
    "green": "#4CAF50",
    "orange": "#FF9800",
}

# ─── Theme styles ─────────────────────────────────────────────────────────────
DARK_THEME = """
QMainWindow { background-color: #1e1e2e; }
QWidget { background-color: #1e1e2e; color: #cdd6f4; font-family: -apple-system, BlinkMacSystemFont, 'SF Pro'; }
QLabel { color: #cdd6f4; }
QPushButton { background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; border-radius: 8px; padding: 8px 16px; font-size: 13px; }
QPushButton:hover { background-color: #45475a; }
QPushButton:pressed { background-color: #585b70; }
QComboBox { background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; border-radius: 6px; padding: 6px 12px; font-size: 12px; }
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView { background-color: #313244; color: #cdd6f4; selection-background-color: #45475a; }
QCheckBox { color: #cdd6f4; font-size: 12px; }
QFrame#separator { background-color: #45475a; }
"""

LIGHT_THEME = """
QMainWindow { background-color: #f5f5f5; }
QWidget { background-color: #f5f5f5; color: #1e1e2e; font-family: -apple-system, BlinkMacSystemFont, 'SF Pro'; }
QLabel { color: #1e1e2e; }
QPushButton { background-color: #ffffff; color: #1e1e2e; border: 1px solid #d0d0d0; border-radius: 8px; padding: 8px 16px; font-size: 13px; }
QPushButton:hover { background-color: #e8e8e8; }
QPushButton:pressed { background-color: #d0d0d0; }
QComboBox { background-color: #ffffff; color: #1e1e2e; border: 1px solid #d0d0d0; border-radius: 6px; padding: 6px 12px; font-size: 12px; }
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView { background-color: #ffffff; color: #1e1e2e; selection-background-color: #e0e0e0; }
QCheckBox { color: #1e1e2e; font-size: 12px; }
QFrame#separator { background-color: #d0d0d0; }
"""


def _make_status_pixmap(color: str, size: int = 12) -> QPixmap:
    """Create a small colored circle pixmap for status indicators."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return pixmap


def _make_tray_icon(color: str) -> QIcon:
    """Create a tray icon with a colored status dot."""
    size = 22
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # Draw ⚡ as text
    painter.setPen(QColor("#cdd6f4"))
    font = QFont("Apple Color Emoji", 14)
    painter.setFont(font)
    painter.drawText(0, 0, size, size, Qt.AlignmentFlag.AlignCenter, "⚡")
    # Draw status dot in bottom-right
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(size - 8, size - 8, 7, 7)
    painter.end()
    return QIcon(pixmap)


class ControlCenter(QMainWindow):
    """Nova Control Center — main launcher window."""

    # Signals
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    sidebar_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nova")
        self.setFixedSize(400, 540)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )

        self._running = False
        self._sidebar_visible = False
        self._dark_mode = True
        self._accent = "cyan"

        # Health status cache
        self._health: dict = {}

        # ─── Central widget ───────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(12)

        # ─── Header ──────────────────────────────────────────────────
        header = QLabel("⚡ Nova")
        header.setFont(QFont(".AppleSystemUIFont", 22, QFont.Weight.Bold))
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        subtitle = QLabel("Local AI Assistant")
        subtitle.setFont(QFont(".AppleSystemUIFont", 12))
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #888;")
        layout.addWidget(subtitle)

        layout.addSpacing(8)

        # ─── Start/Stop button ────────────────────────────────────────
        self._start_btn = QPushButton("▶  Start Nova")
        self._start_btn.setFixedHeight(44)
        self._start_btn.setFont(QFont(".AppleSystemUIFont", 14, QFont.Weight.DemiBold))
        self._start_btn.clicked.connect(self._toggle_running)
        layout.addWidget(self._start_btn)

        # ─── Status section ───────────────────────────────────────────
        layout.addSpacing(4)
        status_label = QLabel("Status")
        status_label.setFont(QFont(".AppleSystemUIFont", 11, QFont.Weight.Bold))
        layout.addWidget(status_label)

        self._ollama_status = self._make_status_row("Ollama")
        layout.addLayout(self._ollama_status["layout"])

        self._vision_status = self._make_status_row("Vision Model")
        layout.addLayout(self._vision_status["layout"])

        self._chat_status = self._make_status_row("Chat Model")
        layout.addLayout(self._chat_status["layout"])

        self._voice_status = self._make_status_row("Voice")
        layout.addLayout(self._voice_status["layout"])

        # ─── Separator ────────────────────────────────────────────────
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # ─── Quick settings ───────────────────────────────────────────
        settings_label = QLabel("Settings")
        settings_label.setFont(QFont(".AppleSystemUIFont", 11, QFont.Weight.Bold))
        layout.addWidget(settings_label)

        # Theme toggle
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme"))
        self._theme_btn = QPushButton("🌙 Dark")
        self._theme_btn.setFixedWidth(90)
        self._theme_btn.clicked.connect(self._toggle_theme)
        theme_row.addStretch()
        theme_row.addWidget(self._theme_btn)
        layout.addLayout(theme_row)

        # Accent color
        accent_row = QHBoxLayout()
        accent_row.addWidget(QLabel("Accent"))
        self._accent_btns: dict[str, QPushButton] = {}
        for name, hex_color in ACCENT_COLORS.items():
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(
                f"background-color: {hex_color}; border-radius: 14px; border: 2px solid transparent;"
            )
            btn.clicked.connect(lambda checked, n=name: self._set_accent(n))
            self._accent_btns[name] = btn
            accent_row.addWidget(btn)
        accent_row.addStretch()
        layout.addLayout(accent_row)

        # Voice selector
        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("Voice"))
        self._voice_combo = QComboBox()
        self._voice_combo.addItems([
            "Daniel", "Samantha", "Alex", "Karen",
            "Moira", "Tessa", "Fiona", "Veena",
        ])
        self._voice_combo.setCurrentText(config.MACOS_SAY_VOICE)
        self._voice_combo.setFixedWidth(140)
        voice_row.addStretch()
        voice_row.addWidget(self._voice_combo)
        layout.addLayout(voice_row)

        # Start on Login
        self._login_checkbox = QCheckBox("Start on Login")
        self._login_checkbox.setChecked(self._is_launchagent_installed())
        self._login_checkbox.toggled.connect(self._toggle_launchagent)
        layout.addWidget(self._login_checkbox)

        # ─── Separator ────────────────────────────────────────────────
        sep2 = QFrame()
        sep2.setObjectName("separator")
        sep2.setFixedHeight(1)
        layout.addWidget(sep2)

        # ─── Open Sidebar button ─────────────────────────────────────
        self._sidebar_btn = QPushButton("📱  Open Sidebar")
        self._sidebar_btn.setFixedHeight(36)
        self._sidebar_btn.setEnabled(False)
        self._sidebar_btn.clicked.connect(self._on_sidebar_clicked)
        layout.addWidget(self._sidebar_btn)

        # ─── Version info ─────────────────────────────────────────────
        layout.addStretch()
        version_label = QLabel(f"v{__version__} • 100% Local • No Cloud")
        version_label.setFont(QFont(".AppleSystemUIFont", 10))
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_label.setStyleSheet("color: #666;")
        layout.addWidget(version_label)

        # ─── System tray ──────────────────────────────────────────────
        self._tray: Optional[QSystemTrayIcon] = None
        self._setup_tray()

        # ─── Status timer ─────────────────────────────────────────────
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(5000)  # Every 5 seconds

        # Apply initial theme
        self._apply_theme()
        self._update_accent_highlight()

        # Initial status check
        QTimer.singleShot(500, self._refresh_status)

    # ──────────────────────────────────────────────────────────────────
    # Status row helper
    # ──────────────────────────────────────────────────────────────────

    def _make_status_row(self, label: str) -> dict:
        """Create a status row with dot indicator and text."""
        row = QHBoxLayout()
        dot = QLabel()
        dot.setFixedSize(14, 14)
        dot.setPixmap(_make_status_pixmap("#666"))
        text = QLabel(f"{label}: Checking...")
        text.setFont(QFont(".AppleSystemUIFont", 11))
        row.addWidget(dot)
        row.addWidget(text)
        row.addStretch()
        return {"layout": row, "dot": dot, "text": text}

    def _set_status_row(self, row: dict, status: str, color: str):
        """Update a status row with new status text and color."""
        row["dot"].setPixmap(_make_status_pixmap(color))
        label_name = row["text"].text().split(":")[0]
        row["text"].setText(f"{label_name}: {status}")

    # ──────────────────────────────────────────────────────────────────
    # Status refresh
    # ──────────────────────────────────────────────────────────────────

    def _refresh_status(self):
        """Refresh all status indicators (called by timer)."""
        try:
            from src.ollama_manager import get_manager
            manager = get_manager()
            self._health = manager.get_health_status()
        except (ImportError, Exception) as exc:
            logger.debug("Health check failed: %s", exc)
            self._health = {
                "ollama_running": False,
                "vision_model_ready": False,
                "chat_model_ready": False,
                "models_loaded": [],
            }

        # Ollama
        if self._health.get("ollama_running"):
            self._set_status_row(self._ollama_status, "Running", "#4CAF50")
        else:
            self._set_status_row(self._ollama_status, "Stopped", "#f44336")

        # Vision model
        if self._health.get("vision_model_ready"):
            self._set_status_row(self._vision_status, "Ready", "#4CAF50")
        elif self._health.get("ollama_running"):
            self._set_status_row(self._vision_status, "Not loaded", "#FF9800")
        else:
            self._set_status_row(self._vision_status, "Unavailable", "#f44336")

        # Chat model
        if self._health.get("chat_model_ready"):
            self._set_status_row(self._chat_status, "Ready", "#4CAF50")
        elif self._health.get("ollama_running"):
            self._set_status_row(self._chat_status, "Not loaded", "#FF9800")
        else:
            self._set_status_row(self._chat_status, "Unavailable", "#f44336")

        # Voice
        if self._running:
            self._set_status_row(self._voice_status, "Listening", "#4CAF50")
        else:
            self._set_status_row(self._voice_status, "Idle", "#666")

        # Update tray icon color
        self._update_tray_status()

    def _get_overall_status_color(self) -> str:
        """Get overall status color: green/yellow/red."""
        if not self._health.get("ollama_running"):
            return "#f44336"  # Red
        if self._health.get("chat_model_ready") and self._health.get("vision_model_ready"):
            return "#4CAF50"  # Green
        return "#FF9800"  # Yellow

    # ──────────────────────────────────────────────────────────────────
    # System tray
    # ──────────────────────────────────────────────────────────────────

    def _setup_tray(self):
        """Set up the system tray icon."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray not available")
            return

        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(_make_tray_icon("#666"))
        self._tray.setToolTip("Nova")

        menu = QMenu()
        show_action = QAction("Show Control Center", self)
        show_action.triggered.connect(self._show_control_center)
        menu.addAction(show_action)

        self._tray_sidebar_action = QAction("Show Sidebar", self)
        self._tray_sidebar_action.triggered.connect(self._on_sidebar_clicked)
        self._tray_sidebar_action.setEnabled(False)
        menu.addAction(self._tray_sidebar_action)

        menu.addSeparator()

        self._tray_toggle_action = QAction("Start", self)
        self._tray_toggle_action.triggered.connect(self._toggle_running)
        menu.addAction(self._tray_toggle_action)

        menu.addSeparator()

        quit_action = QAction("Quit Nova", self)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _update_tray_status(self):
        """Update tray icon with current status color."""
        if self._tray:
            color = self._get_overall_status_color()
            self._tray.setIcon(_make_tray_icon(color))

    def _on_tray_activated(self, reason):
        """Handle tray icon click."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_control_center()

    def _show_control_center(self):
        """Show and raise the control center window."""
        self.show()
        self.raise_()
        self.activateWindow()

    # ──────────────────────────────────────────────────────────────────
    # Actions
    # ──────────────────────────────────────────────────────────────────

    def _toggle_running(self):
        """Toggle the Nova running state."""
        if self._running:
            self._running = False
            self._start_btn.setText("▶  Start Nova")
            self._sidebar_btn.setEnabled(False)
            self._tray_toggle_action.setText("Start")
            self._tray_sidebar_action.setEnabled(False)
            self.stop_requested.emit()
        else:
            self._running = True
            self._start_btn.setText("⏹  Stop Nova")
            self._sidebar_btn.setEnabled(True)
            self._tray_toggle_action.setText("Stop")
            self._tray_sidebar_action.setEnabled(True)
            self.start_requested.emit()

    def _on_sidebar_clicked(self):
        """Handle sidebar button click."""
        self._sidebar_visible = not self._sidebar_visible
        if self._sidebar_visible:
            self._sidebar_btn.setText("📱  Hide Sidebar")
            self._tray_sidebar_action.setText("Hide Sidebar")
        else:
            self._sidebar_btn.setText("📱  Show Sidebar")
            self._tray_sidebar_action.setText("Show Sidebar")
        self.sidebar_requested.emit()

    def set_sidebar_visible(self, visible: bool):
        """Update sidebar button state from external source."""
        self._sidebar_visible = visible
        if visible:
            self._sidebar_btn.setText("📱  Hide Sidebar")
            if self._tray:
                self._tray_sidebar_action.setText("Hide Sidebar")
        else:
            self._sidebar_btn.setText("📱  Show Sidebar")
            if self._tray:
                self._tray_sidebar_action.setText("Show Sidebar")

    # ──────────────────────────────────────────────────────────────────
    # Theme & Accent
    # ──────────────────────────────────────────────────────────────────

    def _toggle_theme(self):
        """Toggle between dark and light theme."""
        self._dark_mode = not self._dark_mode
        self._apply_theme()

    def _apply_theme(self):
        """Apply the current theme."""
        if self._dark_mode:
            self.setStyleSheet(DARK_THEME)
            self._theme_btn.setText("🌙 Dark")
        else:
            self.setStyleSheet(LIGHT_THEME)
            self._theme_btn.setText("☀️ Light")

    def _set_accent(self, name: str):
        """Set the accent color."""
        self._accent = name
        self._update_accent_highlight()

    def _update_accent_highlight(self):
        """Highlight the selected accent color button."""
        for name, btn in self._accent_btns.items():
            hex_color = ACCENT_COLORS[name]
            if name == self._accent:
                btn.setStyleSheet(
                    f"background-color: {hex_color}; border-radius: 14px; border: 2px solid #ffffff;"
                )
            else:
                btn.setStyleSheet(
                    f"background-color: {hex_color}; border-radius: 14px; border: 2px solid transparent;"
                )

    @property
    def accent_color(self) -> str:
        """Get the current accent color name."""
        return self._accent

    @property
    def selected_voice(self) -> str:
        """Get the currently selected voice."""
        return self._voice_combo.currentText()

    @property
    def is_dark_mode(self) -> bool:
        """Get the current theme mode."""
        return self._dark_mode

    # ──────────────────────────────────────────────────────────────────
    # LaunchAgent
    # ──────────────────────────────────────────────────────────────────

    _PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/com.nova.plist")

    def _is_launchagent_installed(self) -> bool:
        """Check if the LaunchAgent plist exists."""
        return os.path.exists(self._PLIST_PATH)

    def _toggle_launchagent(self, checked: bool):
        """Install or uninstall the LaunchAgent."""
        scripts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
        if checked:
            script = os.path.join(scripts_dir, "install-launchagent.sh")
            if os.path.exists(script):
                subprocess.run(["bash", script], check=False)
        else:
            script = os.path.join(scripts_dir, "uninstall-launchagent.sh")
            if os.path.exists(script):
                subprocess.run(["bash", script], check=False)

    # ──────────────────────────────────────────────────────────────────
    # Window events
    # ──────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        """Minimize to tray on close (don't quit)."""
        if self._tray and self._tray.isVisible():
            self.hide()
            event.ignore()
        else:
            event.accept()

    def _quit_app(self):
        """Quit the entire application."""
        if self._running:
            self.stop_requested.emit()
        if self._tray:
            self._tray.hide()
        QApplication.instance().quit()
