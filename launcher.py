#!/usr/bin/env python3
"""
Nova Launcher — Control Center entry point.

This is the primary entry point for the .app bundle and GUI users.
It shows the Control Center window which can then launch the full
Nova experience (sidebar + voice + vision).

For terminal-only usage, `python nova.py` still works directly.

Usage:
    python launcher.py          # Opens Control Center
    python launcher.py --help   # Show options
"""

import logging
import os
import signal
import sys
import threading

# Ensure project root is on path
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config


def main():
    """Launch the Nova Control Center."""
    # ── Logging ───────────────────────────────────────────────────────
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger("nova.launcher")

    # Pipe safety
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, OSError):
        pass

    # ── PyQt6 Application ─────────────────────────────────────────────
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QTimer

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Nova")
    app.setOrganizationName("Escipion")
    app.setQuitOnLastWindowClosed(False)  # Keep running in tray

    # ── Control Center ────────────────────────────────────────────────
    from src.control_center import ControlCenter

    control_center = ControlCenter()

    # ── State ─────────────────────────────────────────────────────────
    _state = {
        "pipeline": None,
        "pipeline_thread": None,
        "sidebar": None,
    }

    # ── Start/Stop handlers ───────────────────────────────────────────
    def _on_start():
        """Start the Nova pipeline (voice + vision)."""
        logger.info("Starting Nova pipeline...")
        try:
            from src.resource_manager import (
                limit_cpu_threads,
                set_process_priority,
                set_ollama_gpu_layers,
            )
            limit_cpu_threads()
            set_process_priority()
            set_ollama_gpu_layers()

            from src.pipeline import NovaPipeline
            pipeline = NovaPipeline()
            _state["pipeline"] = pipeline

            # Apply voice from control center
            voice = control_center.selected_voice
            if hasattr(pipeline, 'tts') and pipeline.tts:
                pipeline.tts.say_voice = voice

            # Start pipeline in background thread
            def run_pipeline():
                try:
                    pipeline.start()
                    pipeline.wait()
                except Exception as exc:
                    logger.error("Pipeline error: %s", exc)

            t = threading.Thread(target=run_pipeline, name="nova-pipeline", daemon=True)
            t.start()
            _state["pipeline_thread"] = t
            logger.info("Nova pipeline started")

        except Exception as exc:
            logger.error("Failed to start pipeline: %s", exc)

    def _on_stop():
        """Stop the Nova pipeline."""
        logger.info("Stopping Nova pipeline...")
        pipeline = _state.get("pipeline")
        if pipeline:
            try:
                if hasattr(pipeline, 'analysis_mode') and pipeline.analysis_mode:
                    pipeline.analysis_mode.stop()
                pipeline.stop()
            except Exception as exc:
                logger.error("Error stopping pipeline: %s", exc)
            _state["pipeline"] = None
            _state["pipeline_thread"] = None

        # Hide sidebar
        sidebar = _state.get("sidebar")
        if sidebar:
            sidebar.hide()
            _state["sidebar"] = None
            control_center.set_sidebar_visible(False)
        logger.info("Nova pipeline stopped")

    def _on_sidebar_requested():
        """Toggle the sidebar visibility."""
        sidebar = _state.get("sidebar")
        if sidebar and sidebar.isVisible():
            sidebar.hide()
            control_center.set_sidebar_visible(False)
        else:
            if sidebar is None:
                try:
                    from src.sidebar import create_sidebar
                    accent = control_center.accent_color
                    sidebar = create_sidebar(accent_color=accent)
                    _state["sidebar"] = sidebar

                    # Wire sidebar to pipeline if running
                    pipeline = _state.get("pipeline")
                    if pipeline and sidebar:
                        pipeline.set_overlay(sidebar)

                except ImportError:
                    logger.warning("Cannot create sidebar — src.sidebar not available")
                    return
                except Exception as exc:
                    logger.warning("Failed to create sidebar: %s", exc)
                    return

            if sidebar:
                sidebar.show()
                control_center.set_sidebar_visible(True)

    # ── Connect signals ───────────────────────────────────────────────
    control_center.start_requested.connect(_on_start)
    control_center.stop_requested.connect(_on_stop)
    control_center.sidebar_requested.connect(_on_sidebar_requested)

    # ── Show window ───────────────────────────────────────────────────
    control_center.show()
    logger.info("Nova Control Center ready")

    # ── Handle shutdown ───────────────────────────────────────────────
    def _shutdown(*_):
        logger.info("Shutting down Nova...")
        _on_stop()
        app.quit()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ── Run ───────────────────────────────────────────────────────────
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
