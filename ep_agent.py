#!/usr/bin/env python3
"""
EP Agent Multimodal Launcher — Voice + Vision + Menu Bar.

Starts the full EP Agent experience:
1. Voice pipeline (wake word → STT → LLM → TTS)
2. Vision system (event-driven screen analysis)
3. Menu bar extra (lightweight status + controls)
4. Optional side-panel overlay (on demand)

Usage:
    python ep_agent.py                    # Full experience (menu bar + voice)
    python ep_agent.py --no-overlay       # Voice only, no GUI at all
    python ep_agent.py --vision-only      # Vision + menu bar only
    python ep_agent.py --check            # Check system readiness

All processing runs 100% locally. No cloud dependencies.
"""

import argparse
import logging
import signal
import sys
import threading

import config
from src.vision import VisionClient


def check_system():
    """Check if all required components are available."""
    print("🔍 EP Agent System Check\n")
    all_ok = True

    # Ollama
    try:
        import requests
        resp = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        print(f"  ✅ Ollama running at {config.OLLAMA_BASE_URL}")
        print(f"     Models: {', '.join(models) if models else 'none'}")

        # Chat model
        chat_found = any(config.OLLAMA_MODEL in m for m in models)
        if chat_found:
            print(f"  ✅ Chat model: {config.OLLAMA_MODEL}")
        else:
            print(f"  ❌ Chat model '{config.OLLAMA_MODEL}' not found")
            print(f"     Fix: ollama pull {config.OLLAMA_MODEL}")
            all_ok = False

        # Vision model
        vision_found = any(config.VISION_MODEL in m for m in models)
        if vision_found:
            print(f"  ✅ Vision model: {config.VISION_MODEL}")
        else:
            print(f"  ⚠️  Vision model '{config.VISION_MODEL}' not found")
            print(f"     Fix: ollama pull {config.VISION_MODEL}")
    except Exception as e:
        print(f"  ❌ Ollama not reachable: {e}")
        print(f"     Fix: ollama serve")
        all_ok = False

    # Screen capture
    print()
    try:
        import mss
        print("  ✅ Screen capture: mss")
    except ImportError:
        try:
            import pyautogui
            print("  ✅ Screen capture: pyautogui (fallback)")
        except ImportError:
            print("  ❌ No screen capture library")
            print("     Fix: pip install mss")
            all_ok = False

    # PIL
    try:
        from PIL import Image
        print("  ✅ PIL/Pillow available")
    except ImportError:
        print("  ❌ Pillow not installed")
        print("     Fix: pip install Pillow")
        all_ok = False

    # rumps (menu bar)
    try:
        import rumps
        print("  ✅ rumps (menu bar)")
    except ImportError:
        print("  ⚠️  rumps not installed (menu bar disabled)")
        print("     Fix: pip install rumps")

    # PyQt6
    try:
        from PyQt6.QtWidgets import QApplication
        print("  ✅ PyQt6 available (overlay UI)")
    except ImportError:
        print("  ⚠️  PyQt6 not installed (overlay UI disabled)")
        print("     Fix: pip install PyQt6")

    # STT
    try:
        from faster_whisper import WhisperModel
        print("  ✅ faster-whisper (STT)")
    except ImportError:
        print("  ⚠️  faster-whisper not installed (voice disabled)")

    # TTS
    import shutil
    if shutil.which("say"):
        print("  ✅ macOS say (TTS)")
    else:
        print("  ⚠️  macOS say not available")

    # Resource info
    print()
    import os
    total_cores = os.cpu_count() or 4
    max_threads = max(2, total_cores // 4)
    print(f"  ℹ️  CPU threads: {max_threads}/{total_cores} (25% cap)")
    print(f"  ℹ️  KV cache flush: every {config.KV_CACHE_FLUSH_INTERVAL}m")
    print(f"  ℹ️  Vision: event-driven (change threshold: {config.VISION_CHANGE_THRESHOLD*100:.0f}%)")

    print()
    if all_ok:
        print("🟢 System ready! Run: python ep_agent.py")
    else:
        print("🟡 Some components missing — fix the ❌ items above")

    return all_ok


def run_multimodal(no_overlay: bool = False, vision_only: bool = False):
    """Launch the full multimodal EP Agent experience."""
    logger = logging.getLogger(__name__)

    # ── Resource optimization (BEFORE heavy imports) ──────────────────
    from src.resource_manager import (
        limit_cpu_threads,
        set_process_priority,
        set_ollama_gpu_layers,
        log_system_info,
    )
    limit_cpu_threads()         # 25% CPU thread cap
    set_process_priority()      # Low priority → never freezes the OS
    set_ollama_gpu_layers()     # Force Metal/CUDA offload
    log_system_info()

    # ── Start voice pipeline (unless vision-only) ─────────────────────
    pipeline = None
    if not vision_only:
        from src.pipeline import EPAgentPipeline
        pipeline = EPAgentPipeline()

    # ── Menu Bar (primary UI on macOS) ────────────────────────────────
    menubar = None
    if config.MENUBAR_ENABLED and not no_overlay:
        from src.menubar import create_menubar, is_available as menubar_available

        if menubar_available():
            def _on_quit():
                if pipeline:
                    pipeline.stop()
                sys.exit(0)

            def _on_toggle_vision():
                if pipeline and pipeline.analysis_mode:
                    pipeline._handle_vision_toggle()

            menubar = create_menubar(
                on_start=lambda: pipeline.start() if pipeline else None,
                on_stop=lambda: pipeline.stop() if pipeline else None,
                on_toggle_vision=_on_toggle_vision,
                on_quit=_on_quit,
            )

            if pipeline:
                pipeline.set_menubar(menubar)

            logger.info("Menu bar app ready")

    # ── Optional Overlay (only if explicitly enabled) ─────────────────
    overlay = None
    dock_glow = None
    app = None

    if config.OVERLAY_ENABLED and not no_overlay:
        try:
            from PyQt6.QtWidgets import QApplication
            from src.overlay import create_overlay
            from src.dock_glow import create_dock_glow

            app = QApplication.instance() or QApplication(sys.argv)

            def on_toggle():
                if pipeline:
                    pipeline._handle_vision_toggle()

            overlay = create_overlay(on_toggle=on_toggle)
            if overlay:
                overlay.show()
                logger.info("Overlay UI started")

            dock_glow = create_dock_glow()
            if dock_glow:
                logger.info("Dock glow indicator ready")

        except ImportError:
            logger.warning("PyQt6 not available — running without overlay")
        except Exception as exc:
            logger.warning("Failed to start UI: %s", exc)

    # ── Wire UI to pipeline ───────────────────────────────────────────
    if pipeline and overlay:
        pipeline.set_overlay(overlay)
    if pipeline and dock_glow:
        pipeline.set_dock_glow(dock_glow)

    # ── Start voice pipeline in background thread ─────────────────────
    if pipeline:
        def run_pipeline():
            try:
                pipeline.start()
                pipeline.wait()
            except Exception as exc:
                logger.error("Pipeline error: %s", exc)

        pipeline_thread = threading.Thread(
            target=run_pipeline,
            name="ep-agent-pipeline",
            daemon=True,
        )
        pipeline_thread.start()
        logger.info("Voice pipeline started")

    # ── Handle shutdown ───────────────────────────────────────────────
    def shutdown(*_):
        logger.info("Shutting down EP Agent...")
        if pipeline:
            if pipeline.analysis_mode:
                pipeline.analysis_mode.stop()
            pipeline.stop()
        if app:
            app.quit()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # ── Run event loop ────────────────────────────────────────────────
    if menubar and not app:
        # Menu bar is the primary event loop (no overlay)
        logger.info("EP Agent ready — menu bar active, say wake word to interact")
        menubar.run()
    elif app and overlay:
        # PyQt event loop (overlay mode)
        logger.info("EP Agent ready (overlay mode) — say wake word or use the overlay toggle")
        sys.exit(app.exec())
    else:
        # No GUI — just wait
        logger.info("EP Agent ready (headless) — say wake word to interact")
        if pipeline:
            pipeline.wait()
        else:
            signal.pause()


def main():
    parser = argparse.ArgumentParser(
        prog="ep-agent",
        description="EP Agent — Voice + Vision + Menu Bar",
    )
    parser.add_argument("--no-overlay", action="store_true", help="Disable all GUI (headless mode)")
    parser.add_argument("--vision-only", action="store_true", help="Vision + UI only (no voice)")
    parser.add_argument("--check", action="store_true", help="Check system readiness")
    parser.add_argument("--log-level", default=config.LOG_LEVEL, help="Log level")

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.check:
        sys.exit(0 if check_system() else 1)

    run_multimodal(no_overlay=args.no_overlay, vision_only=args.vision_only)


if __name__ == "__main__":
    main()
