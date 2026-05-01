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
    python ep_agent.py --reset            # Reset profile, re-trigger onboarding

All processing runs 100% locally. No cloud dependencies.
"""

import argparse
import logging
import os
import signal
import sys
import threading

import config
from src.vision import VisionClient

# Error log file for crash reports
_ERROR_LOG = os.path.expanduser("~/.ep-agent/error.log")


def _setup_error_logging():
    """Ensure error log directory exists."""
    os.makedirs(os.path.dirname(_ERROR_LOG), exist_ok=True)


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

    try:
        _run_multimodal_inner(no_overlay, vision_only, logger)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as exc:
        # Log to error file and exit cleanly — no crash dump to stderr
        import traceback
        try:
            with open(_ERROR_LOG, "a") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"Crash at {__import__('datetime').datetime.now().isoformat()}\n")
                traceback.print_exc(file=f)
        except OSError:
            pass
        logger.error("Fatal error: %s (logged to %s)", exc, _ERROR_LOG)
        sys.exit(1)


def _run_multimodal_inner(no_overlay: bool, vision_only: bool, logger):
    """Inner implementation — wrapped by run_multimodal for crash safety."""

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

    # ── Menu Bar (DISABLED — sidebar replaces it) ───────────────────
    menubar = None
    # Menu bar (yellow mic icon) is disabled — sidebar is now primary UI

    # ── Load profile (onboarding) ──────────────────────────────────────
    from src.onboarding import run_onboarding, PERSONALITIES, load_profile
    profile = load_profile()

    # ── Sidebar (always-on side panel) ─────────────────────────────────
    sidebar = None
    dock_glow = None
    app = None

    if not no_overlay:
        try:
            from PyQt6.QtWidgets import QApplication
            from src.sidebar import create_sidebar

            # QApplication MUST be created on the main thread (macOS Cocoa requirement)
            app = QApplication.instance() or QApplication(sys.argv)

            # Run onboarding if needed (must happen after QApplication for dialog)
            if profile is None:
                profile = run_onboarding()

            # Extract profile settings
            accent_color = profile.get("accent_color", "cyan") if profile else "cyan"
            personality = profile.get("personality", "friendly") if profile else "friendly"
            voice = profile.get("voice", config.MACOS_SAY_VOICE) if profile else config.MACOS_SAY_VOICE

            # Apply voice to pipeline
            if pipeline and voice:
                pipeline.tts.say_voice = voice

            # Apply personality to LLM system prompt
            if pipeline and personality in PERSONALITIES:
                pipeline.llm.system_prompt = PERSONALITIES[personality]["system_prompt"]

            # Create sidebar on main thread (critical for NSWindow)
            sidebar = create_sidebar(
                accent_color=accent_color,
                personality=personality,
            )
            if sidebar:
                sidebar.show()
                logger.info("Sidebar UI started (right 15%% of screen)")

                # Connect settings button to re-open onboarding
                def _on_settings_requested():
                    from src.onboarding import OnboardingDialog, save_profile
                    dialog = OnboardingDialog(sidebar)
                    if dialog.exec():
                        new_profile = dialog.get_profile()
                        save_profile(new_profile)
                        # Apply changes live
                        sidebar.apply_accent_color(new_profile.get("accent_color", "cyan"))
                        sidebar.apply_personality(new_profile.get("personality", "friendly"))
                        if pipeline:
                            new_voice = new_profile.get("voice", config.MACOS_SAY_VOICE)
                            pipeline.tts.say_voice = new_voice
                            new_pers = new_profile.get("personality", "friendly")
                            if new_pers in PERSONALITIES:
                                pipeline.llm.system_prompt = PERSONALITIES[new_pers]["system_prompt"]

                sidebar.settings_requested.connect(_on_settings_requested)

            # Dock glow removed — no bottom-screen wave animation

        except ImportError:
            logger.warning("PyQt6 not available — running without sidebar")
        except Exception as exc:
            logger.warning("Failed to start UI: %s — falling back to headless", exc)
            app = None
            sidebar = None
    else:
        # Headless mode — still load profile for voice/personality
        if profile is None:
            profile = run_onboarding()
        if pipeline and profile:
            voice = profile.get("voice", config.MACOS_SAY_VOICE)
            personality = profile.get("personality", "friendly")
            pipeline.tts.say_voice = voice
            if personality in PERSONALITIES:
                pipeline.llm.system_prompt = PERSONALITIES[personality]["system_prompt"]

    # ── Wire UI to pipeline ───────────────────────────────────────────
    if pipeline and sidebar:
        pipeline.set_overlay(sidebar)

        # Wire chat input to pipeline (background thread, streaming tokens)
        def _handle_chat_message(text: str):
            def _process():
                try:
                    sidebar.set_status("processing")
                    # Create empty agent bubble, then stream into it
                    sidebar.push_transcript("agent", "")
                    accumulated = ""
                    got_tokens = False
                    for token in pipeline.llm.chat_stream(text):
                        accumulated += token
                        got_tokens = True
                        sidebar.update_last_transcript(accumulated)
                    if not got_tokens:
                        sidebar.update_last_transcript("Sorry, I'm having trouble thinking right now.")
                except Exception as exc:
                    logger.error("Chat processing error: %s", exc)
                    sidebar.update_last_transcript("An error occurred.")
                finally:
                    sidebar.set_status("idle")
            threading.Thread(target=_process, daemon=True, name="chat-handler").start()

        sidebar.chat_message_sent.connect(_handle_chat_message)

        # Wire voice selector to TTS
        def _handle_voice_change(voice_name: str):
            if hasattr(pipeline, 'tts') and pipeline.tts:
                pipeline.tts.say_voice = voice_name
                logger.info("Voice changed to: %s", voice_name)

        sidebar.voice_changed.connect(_handle_voice_change)


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
        # Menu bar is the primary event loop (no sidebar)
        logger.info("EP Agent ready — menu bar active, say wake word to interact")
        menubar.run()
    elif app and sidebar:
        # PyQt event loop (sidebar mode) — main thread stays here
        logger.info("EP Agent ready (sidebar mode) — Cmd+Shift+E to toggle, say wake word to interact")
        try:
            sys.exit(app.exec())
        except Exception as exc:
            # If Qt event loop crashes, fall back to headless
            logger.warning("Qt event loop failed: %s — switching to headless", exc)
            if pipeline:
                pipeline.wait()
            else:
                signal.pause()
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
    parser.add_argument("--reset", action="store_true", help="Reset profile (re-trigger onboarding)")
    parser.add_argument("--log-level", default=config.LOG_LEVEL, help="Log level")

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # Pipe safety (prevents BrokenPipeError in LaunchAgent context)
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, OSError):
        pass  # SIGPIPE not available on Windows

    _setup_error_logging()

    if args.check:
        sys.exit(0 if check_system() else 1)

    if args.reset:
        from src.onboarding import reset_profile
        reset_profile()
        print("✅ Profile reset — onboarding will re-run on next launch")
        # Continue to launch (will show onboarding)

    run_multimodal(no_overlay=args.no_overlay, vision_only=args.vision_only)


if __name__ == "__main__":
    main()
