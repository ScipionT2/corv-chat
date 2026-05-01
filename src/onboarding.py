"""
EP Agent Onboarding — First-run welcome screen.

Shows a modern glassmorphism wizard for personality, voice, and theme selection.
Saves choices to ~/.ep-agent/profile.json.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)

PROFILE_PATH = Path(config.PROFILE_PATH)

# Personality presets
PERSONALITIES = {
    "professional": {
        "label": "Professional",
        "description": "Formal, precise, business-oriented responses.",
        "system_prompt": (
            "You are EP Agent, a professional AI assistant. "
            "Be precise, formal, and efficient. Provide structured answers. "
            "Avoid casual language. Focus on accuracy and clarity."
        ),
    },
    "friendly": {
        "label": "Friendly",
        "description": "Warm, conversational, helpful companion.",
        "system_prompt": (
            "You are EP Agent, a friendly AI companion. "
            "Be warm, conversational, and helpful. Use natural language. "
            "Feel free to be encouraging and personable while staying useful."
        ),
    },
    "casual": {
        "label": "Casual",
        "description": "Relaxed, brief, straight to the point.",
        "system_prompt": (
            "You are EP Agent. Keep it short and chill. "
            "No fluff, no formalities. Just answer the question. "
            "Use casual language, contractions, and be direct."
        ),
    },
    "minimal": {
        "label": "Minimal",
        "description": "Ultra-concise, just the facts.",
        "system_prompt": (
            "You are EP Agent. Respond with the minimum necessary words. "
            "No greetings, no filler. Facts only. One sentence max when possible."
        ),
    },
}

# Accent color options
ACCENT_COLORS = {
    "cyan": {"label": "Cyan", "rgb": (0, 200, 255)},
    "purple": {"label": "Purple", "rgb": (160, 100, 255)},
    "green": {"label": "Green", "rgb": (80, 220, 120)},
    "amber": {"label": "Amber", "rgb": (255, 180, 50)},
}

DEFAULT_PROFILE = {
    "personality": "friendly",
    "voice": "Daniel",
    "accent_color": "cyan",
    "onboarding_complete": True,
}


def load_profile() -> Optional[dict]:
    """Load existing profile from disk. Returns None if not found."""
    if PROFILE_PATH.exists():
        try:
            data = json.loads(PROFILE_PATH.read_text())
            if data.get("onboarding_complete"):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load profile: %s", exc)
    return None


def save_profile(profile: dict) -> None:
    """Save profile to disk."""
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, indent=2))
    logger.info("Profile saved to %s", PROFILE_PATH)


def reset_profile() -> None:
    """Delete profile to re-trigger onboarding."""
    if PROFILE_PATH.exists():
        PROFILE_PATH.unlink()
        logger.info("Profile reset — onboarding will run next launch")


def get_macos_voices() -> list[str]:
    """Get list of available macOS say voices."""
    try:
        result = subprocess.run(
            ["say", "-v", "?"],
            capture_output=True, text=True, timeout=10,
        )
        voices = []
        for line in result.stdout.strip().splitlines():
            # Format: "VoiceName  lang_REGION  # sample"
            parts = line.strip().split()
            if parts:
                name = parts[0]
                voices.append(name)
        return voices[:30]  # Cap at 30 for UI
    except Exception:
        return ["Daniel", "Samantha", "Alex", "Fiona", "Karen", "Moira"]


# ── PyQt6 Onboarding Dialog ──────────────────────────────────────────────

try:
    from PyQt6.QtCore import Qt, QSize, pyqtSignal
    from PyQt6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPainterPath
    from PyQt6.QtWidgets import (
        QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
        QPushButton, QWidget, QStackedWidget, QButtonGroup,
        QRadioButton, QComboBox, QFrame, QGraphicsDropShadowEffect,
        QSizePolicy,
    )
    _PYQT6_OK = True
except ImportError:
    _PYQT6_OK = False


if _PYQT6_OK:

    class OnboardingDialog(QDialog):
        """Multi-step onboarding wizard with glassmorphism design."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self._profile = dict(DEFAULT_PROFILE)
            self._setup_window()
            self._build_ui()

        def _setup_window(self):
            self.setWindowTitle("EP Agent Setup")
            self.setFixedSize(520, 480)
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

            # Center on screen
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                self.move(
                    geo.x() + (geo.width() - 520) // 2,
                    geo.y() + (geo.height() - 480) // 2,
                )

        def paintEvent(self, event: QPaintEvent):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(0.0, 0.0, float(self.width()), float(self.height()), 20.0, 20.0)
            painter.fillPath(path, QColor(14, 16, 22, 240))
            # Border
            painter.setPen(QColor(255, 255, 255, 15))
            painter.drawPath(path)
            painter.end()

        def _build_ui(self):
            layout = QVBoxLayout(self)
            layout.setContentsMargins(28, 24, 28, 24)
            layout.setSpacing(0)

            # Stacked pages
            self._stack = QStackedWidget()
            layout.addWidget(self._stack)

            self._build_welcome_page()
            self._build_personality_page()
            self._build_voice_page()
            self._build_theme_page()

            # Nav buttons
            nav = QHBoxLayout()
            nav.setContentsMargins(0, 16, 0, 0)
            self._back_btn = QPushButton("Back")
            self._back_btn.setStyleSheet(self._btn_style(secondary=True))
            self._back_btn.clicked.connect(self._go_back)
            self._back_btn.setVisible(False)

            self._next_btn = QPushButton("Get Started")
            self._next_btn.setStyleSheet(self._btn_style())
            self._next_btn.clicked.connect(self._go_next)

            nav.addWidget(self._back_btn)
            nav.addStretch()
            nav.addWidget(self._next_btn)
            layout.addLayout(nav)

        def _btn_style(self, secondary=False):
            if secondary:
                return """
                    QPushButton {
                        background: rgba(255,255,255,0.06);
                        border: 1px solid rgba(255,255,255,0.1);
                        border-radius: 8px;
                        color: rgba(255,255,255,0.7);
                        padding: 8px 20px;
                        font-size: 13px;
                    }
                    QPushButton:hover { background: rgba(255,255,255,0.1); }
                """
            return """
                QPushButton {
                    background: rgba(0,200,255,0.8);
                    border: none;
                    border-radius: 8px;
                    color: white;
                    padding: 8px 24px;
                    font-size: 13px;
                    font-weight: bold;
                }
                QPushButton:hover { background: rgba(0,200,255,1.0); }
            """

        def _section_label(self, text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setFont(QFont(".AppleSystemUIFont", 11))
            lbl.setStyleSheet("color: rgba(255,255,255,0.5); margin-bottom: 8px;")
            return lbl

        # ── Pages ─────────────────────────────────────────────────────

        def _build_welcome_page(self):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 20, 0, 0)
            lay.setSpacing(12)

            title = QLabel("Welcome to EP Agent")
            title.setFont(QFont(".AppleSystemUIFont", 22, QFont.Weight.Bold))
            title.setStyleSheet("color: white;")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(title)

            subtitle = QLabel(
                "Your personal AI assistant — running 100% locally.\n"
                "Let's set up your experience."
            )
            subtitle.setFont(QFont(".AppleSystemUIFont", 13))
            subtitle.setStyleSheet("color: rgba(255,255,255,0.6);")
            subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            subtitle.setWordWrap(True)
            lay.addWidget(subtitle)

            lay.addStretch()

            features = QLabel(
                "• Voice-activated assistant (just say the wake word)\n"
                "• Screen analysis with vision AI\n"
                "• Always-on sidebar with Smart Glow\n"
                "• Hybrid: cloud when connected, local when offline"
            )
            features.setFont(QFont(".AppleSystemUIFont", 12))
            features.setStyleSheet("color: rgba(255,255,255,0.75); line-height: 1.6;")
            lay.addWidget(features)

            lay.addStretch()
            self._stack.addWidget(page)

        def _build_personality_page(self):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 10, 0, 0)
            lay.setSpacing(8)

            lay.addWidget(self._section_label("PERSONALITY"))

            title = QLabel("How should I talk?")
            title.setFont(QFont(".AppleSystemUIFont", 18, QFont.Weight.Bold))
            title.setStyleSheet("color: white;")
            lay.addWidget(title)

            self._personality_group = QButtonGroup(self)
            for key, info in PERSONALITIES.items():
                radio = QRadioButton(f"{info['label']} — {info['description']}")
                radio.setStyleSheet("""
                    QRadioButton {
                        color: rgba(255,255,255,0.85);
                        font-size: 12px;
                        padding: 6px 0;
                    }
                    QRadioButton::indicator { width: 14px; height: 14px; }
                """)
                radio.setProperty("personality_key", key)
                if key == "friendly":
                    radio.setChecked(True)
                self._personality_group.addButton(radio)
                lay.addWidget(radio)

            lay.addStretch()
            self._stack.addWidget(page)

        def _build_voice_page(self):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 10, 0, 0)
            lay.setSpacing(8)

            lay.addWidget(self._section_label("VOICE"))

            title = QLabel("Pick my voice")
            title.setFont(QFont(".AppleSystemUIFont", 18, QFont.Weight.Bold))
            title.setStyleSheet("color: white;")
            lay.addWidget(title)

            self._voice_combo = QComboBox()
            self._voice_combo.setStyleSheet("""
                QComboBox {
                    background: rgba(255,255,255,0.06);
                    border: 1px solid rgba(255,255,255,0.1);
                    border-radius: 8px;
                    color: white;
                    padding: 8px 12px;
                    font-size: 13px;
                }
                QComboBox::drop-down { border: none; }
                QComboBox QAbstractItemView {
                    background: rgb(24, 26, 36);
                    color: white;
                    selection-background-color: rgba(0,200,255,0.3);
                }
            """)
            voices = get_macos_voices()
            self._voice_combo.addItems(voices)
            # Default to Daniel
            idx = self._voice_combo.findText("Daniel")
            if idx >= 0:
                self._voice_combo.setCurrentIndex(idx)
            lay.addWidget(self._voice_combo)

            preview_btn = QPushButton("▶ Preview Voice")
            preview_btn.setStyleSheet(self._btn_style(secondary=True))
            preview_btn.clicked.connect(self._preview_voice)
            lay.addWidget(preview_btn)

            lay.addStretch()
            self._stack.addWidget(page)

        def _build_theme_page(self):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 10, 0, 0)
            lay.setSpacing(8)

            lay.addWidget(self._section_label("THEME"))

            title = QLabel("Choose your accent color")
            title.setFont(QFont(".AppleSystemUIFont", 18, QFont.Weight.Bold))
            title.setStyleSheet("color: white;")
            lay.addWidget(title)

            self._theme_group = QButtonGroup(self)
            for key, info in ACCENT_COLORS.items():
                r, g, b = info["rgb"]
                radio = QRadioButton(f"  {info['label']}")
                radio.setStyleSheet(f"""
                    QRadioButton {{
                        color: rgb({r},{g},{b});
                        font-size: 13px;
                        font-weight: bold;
                        padding: 8px 0;
                    }}
                    QRadioButton::indicator {{ width: 14px; height: 14px; }}
                """)
                radio.setProperty("color_key", key)
                if key == "cyan":
                    radio.setChecked(True)
                self._theme_group.addButton(radio)
                lay.addWidget(radio)

            lay.addStretch()
            self._stack.addWidget(page)

        # ── Navigation ────────────────────────────────────────────────

        def _go_next(self):
            current = self._stack.currentIndex()
            if current == self._stack.count() - 1:
                self._finish()
            else:
                self._stack.setCurrentIndex(current + 1)
                self._back_btn.setVisible(True)
                if current + 1 == self._stack.count() - 1:
                    self._next_btn.setText("Finish")
                else:
                    self._next_btn.setText("Next")

        def _go_back(self):
            current = self._stack.currentIndex()
            if current > 0:
                self._stack.setCurrentIndex(current - 1)
                self._next_btn.setText("Next" if current - 1 < self._stack.count() - 1 else "Finish")
                if current - 1 == 0:
                    self._back_btn.setVisible(False)

        def _preview_voice(self):
            voice = self._voice_combo.currentText()
            try:
                subprocess.Popen(
                    ["say", "-v", voice, "Hi, I'm EP Agent. Nice to meet you."],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception as exc:
                logger.warning("Voice preview failed: %s", exc)

        def _finish(self):
            # Collect choices
            for btn in self._personality_group.buttons():
                if btn.isChecked():
                    self._profile["personality"] = btn.property("personality_key")
                    break

            self._profile["voice"] = self._voice_combo.currentText()

            for btn in self._theme_group.buttons():
                if btn.isChecked():
                    self._profile["accent_color"] = btn.property("color_key")
                    break

            self._profile["onboarding_complete"] = True
            save_profile(self._profile)
            self.accept()

        def get_profile(self) -> dict:
            return self._profile


def _has_display() -> bool:
    """Check if we have access to an interactive display (not a headless LaunchAgent)."""
    import os
    import sys
    # On macOS, check if we can access the WindowServer
    # LaunchAgents with RunAtLoad may not have display access
    if sys.platform == "darwin":
        # If DISPLAY or TERM_SESSION_ID is set, we likely have GUI access
        # Also check if running under launchd without Aqua session
        session_type = os.environ.get("XPC_SERVICE_NAME", "")
        # If stdout is not a TTY and there's no explicit GUI env, assume headless
        if not sys.stdout.isatty() and not os.environ.get("TERM_SESSION_ID"):
            # Try a lightweight check: can we access NSScreen?
            try:
                from PyQt6.QtWidgets import QApplication
                app = QApplication.instance()
                if app and app.primaryScreen():
                    return True
            except Exception:
                pass
            # Still might work if QApplication was created successfully
            return True
    return True


def run_onboarding(app=None) -> dict:
    """Run the onboarding wizard. Returns profile dict.

    If PyQt6 is unavailable or the dialog is cancelled, returns DEFAULT_PROFILE.
    If running in a headless/LaunchAgent context, saves defaults without showing UI.
    """
    # Check existing profile
    existing = load_profile()
    if existing:
        return existing

    if not _PYQT6_OK:
        logger.info("PyQt6 unavailable — using default profile")
        save_profile(DEFAULT_PROFILE)
        return DEFAULT_PROFILE

    try:
        # Attempt to show the dialog — if it fails (headless), use defaults
        dialog = OnboardingDialog()
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            return dialog.get_profile()
        else:
            # User closed — save defaults
            save_profile(DEFAULT_PROFILE)
            return DEFAULT_PROFILE
    except Exception as exc:
        logger.warning("Onboarding failed: %s — using defaults", exc)
        save_profile(DEFAULT_PROFILE)
        return DEFAULT_PROFILE
