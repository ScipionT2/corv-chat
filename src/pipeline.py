"""
Nova Pipeline — orchestrates the full voice-interaction loop.

Wake word → Record → Transcribe → LLM → Speak

Includes KV cache management and event-driven vision integration.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Optional

import requests

import config
from src.app_detector import get_active_app
from src.commands import CommandResult, parse_command
from src.llm import OllamaClient
from src.providers import MultiProviderLLM
from src.memory.router import MemoryRouter
from src.ollama_manager import get_manager
from src.recorder import record_until_silence
from src.resource_manager import KVCacheTimer
from src.stt import SpeechToText
from src.tts import TextToSpeech
from src.vision import AnalysisMode, VisionClient, VisionResult
from src.vision_history import save_analysis as _save_vision_history
from src.vision_prompts import categorize_suggestion, select_prompt_for_app
from src.wake_word import WakeWordDetector

logger = logging.getLogger(__name__)


class NovaPipeline:
    """End-to-end voice assistant pipeline.

    Parameters
    ----------
    wake_word:
        Wake-word string (OpenWakeWord model name).
    ollama_model:
        Ollama model tag.
    whisper_model:
        Whisper model size / name.
    tts_backend:
        TTS backend preference (``'auto'``, ``'piper'``, ``'say'``).
    voice:
        macOS ``say`` voice name override.  ``None`` uses the default.
    """

    def __init__(
        self,
        wake_word: str = config.WAKE_WORD,
        ollama_model: str = config.OLLAMA_MODEL,
        whisper_model: str = config.WHISPER_MODEL,
        tts_backend: str = config.TTS_BACKEND,
        voice: Optional[str] = None,
        enable_vision: bool = config.VISION_ENABLED,
        hybrid_mode: bool = True,
    ) -> None:
        self.stt = SpeechToText(model_name=whisper_model)

        # Multi-provider LLM: prioritised cloud providers with Ollama fallback
        if not config.OFFLINE_MODE:
            self.llm = MultiProviderLLM(
                on_provider_change=self._on_llm_mode_change,
            )
        else:
            self.llm = OllamaClient(model=ollama_model)

        # Wrap LLM with 3-tier memory system (graceful fallback to self.llm)
        self.memory_router = MemoryRouter(fallback_llm=self.llm)

        self.tts = TextToSpeech(backend=tts_backend, say_voice=voice)
        self.detector: Optional[WakeWordDetector] = None

        # Vision subsystem (only loaded when enabled)
        self._vision_enabled = enable_vision
        if enable_vision:
            self.vision_client = VisionClient()
            self.analysis_mode = AnalysisMode(
                on_result=self._on_vision_result,
                interval=config.VISION_INTERVAL,
                monitor=config.VISION_MONITOR,
                scale=config.VISION_SCALE,
                vision_client=self.vision_client,
            )
            logger.info("Vision subsystem enabled (event-driven)")

            # Pre-warm models in background (non-blocking)
            manager = get_manager()
            manager.warmup_models()
        else:
            self.vision_client = None
            self.analysis_mode = None
            logger.info("Vision subsystem disabled (lightweight mode)")

        # KV Cache management
        self._kv_timer = KVCacheTimer(
            interval_minutes=config.KV_CACHE_FLUSH_INTERVAL,
            model=ollama_model,
        )

        self._overlay = None
        self._dock_glow = None
        self._menubar = None

        self._wake_word = wake_word
        self._running = False
        self._stop_event = threading.Event()
        self._interrupted = threading.Event()  # Set when wake word fires during TTS

        # Watchdog / crash recovery state
        self._ollama_healthy = True
        self._restart_timestamps: deque[float] = deque()
        self._watchdog_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _on_llm_mode_change(self, mode: str):
        """Callback when hybrid LLM switches between cloud/local."""
        if self._overlay and hasattr(self._overlay, 'set_connectivity'):
            self._overlay.set_connectivity(mode)
        logger.info("LLM connectivity: %s", mode)

    def start(self) -> None:
        """Initialise components and begin listening for the wake word."""
        logger.info("Starting Nova pipeline …")

        # Pre-load the Whisper model so first interaction is fast
        self.stt.load()

        self.detector = WakeWordDetector(
            on_wake=self.on_wake,
            wake_word=self._wake_word,
        )
        self.detector.start()

        # Start KV cache timer
        self._kv_timer.start()

        # Start hybrid LLM heartbeat if available
        if hasattr(self.llm, 'start'):
            self.llm.start()

        # Start memory router
        self.memory_router.start()

        self._running = True
        self._stop_event.clear()

        # Start watchdog thread
        if config.WATCHDOG_ENABLED:
            self._watchdog_thread = threading.Thread(
                target=self._watchdog,
                name="nova-watchdog",
                daemon=True,
            )
            self._watchdog_thread.start()
            logger.info("Watchdog thread started (interval=%ds)", config.WATCHDOG_INTERVAL)

        if self._menubar:
            self._menubar.set_pipeline_running(True)

        logger.info("Nova pipeline is running — say '%s' to begin", self._wake_word)

    def stop(self) -> None:
        """Cleanly shut everything down."""
        logger.info("Stopping Nova pipeline …")
        self._running = False
        self._stop_event.set()
        if self.detector is not None:
            self.detector.stop()
        if self.analysis_mode is not None:
            self.analysis_mode.stop()
        self._kv_timer.stop()

        # Stop memory router (triggers final digestion)
        self.memory_router.stop()

        # Stop hybrid LLM heartbeat
        if hasattr(self.llm, 'stop'):
            self.llm.stop()

        if self._menubar:
            self._menubar.set_pipeline_running(False)

        logger.info("Nova pipeline stopped")

    def wait(self) -> None:
        """Block until :meth:`stop` is called."""
        self._stop_event.wait()

    # ------------------------------------------------------------------
    # Core interaction
    # ------------------------------------------------------------------

    def _on_interrupt(self) -> None:
        """Called when wake word fires during TTS playback.

        Sets the interrupt flag and kills TTS so on_wake can restart.
        """
        logger.info("Interrupt detected — stopping TTS and re-entering listen mode")
        self._interrupted.set()
        self.tts.stop()

    def _speak_interruptible(self, text: str) -> bool:
        """Speak text while keeping wake word detection active.

        Returns ``True`` if speech completed normally, ``False`` if it was
        interrupted by a wake word detection.
        """
        self._interrupted.clear()

        # Re-enable wake word detection during speech, with interrupt callback
        if self.detector is not None:
            original_on_wake = self.detector.on_wake
            self.detector.on_wake = self._on_interrupt
            self.detector._paused = False

        try:
            self.tts.speak(text)
        finally:
            # Restore original callback and pause state
            if self.detector is not None:
                self.detector._paused = True
                self.detector.on_wake = original_on_wake

        was_interrupted = self._interrupted.is_set()
        if was_interrupted:
            logger.info("Speech was interrupted by wake word")
        return not was_interrupted

    def _speak_streamed_interruptible(self, token_generator) -> tuple[bool, str]:
        """Speak streamed tokens while keeping wake word detection active.

        Uses a concurrent :class:`StreamingTTS` session so that TTS
        playback of one sentence overlaps with LLM generation of the
        next.  This significantly reduces perceived latency compared to
        the previous synchronous approach.

        Parameters
        ----------
        token_generator:
            An iterable that yields string tokens from the LLM.

        Returns
        -------
        tuple[bool, str]
            ``(completed, full_text)`` — *completed* is ``True`` if all
            speech finished without interruption; *full_text* is the
            accumulated reply for transcript/history purposes.
        """
        self._interrupted.clear()

        # Create a concurrent streaming TTS session
        stream = self.tts.create_streaming_session()

        # Re-enable wake word detection during speech, with interrupt callback
        if self.detector is not None:
            original_on_wake = self.detector.on_wake
            self.detector.on_wake = self._on_interrupt
            self.detector._paused = False

        completed = True

        try:
            for token in token_generator:
                if self._interrupted.is_set():
                    # Stop speaking but keep draining tokens so the
                    # generator can finalise history in the LLM client.
                    stream.stop()
                    completed = False
                    # Still accumulate full text for history
                    stream._full_text += token
                    continue

                stream.add_token(token)

            # Flush remaining buffer and wait for all speech to finish
            if not self._interrupted.is_set():
                stream.finish()
                if self._interrupted.is_set():
                    completed = False
            else:
                completed = False
        except Exception as exc:  # noqa: BLE001
            logger.error("Streamed speech error: %s", exc)
            completed = False
            stream.stop()
        finally:
            # Ensure worker thread is cleaned up
            stream.shutdown()
            # Restore original callback and pause state
            if self.detector is not None:
                self.detector._paused = True
                self.detector.on_wake = original_on_wake

        if not completed:
            logger.info("Streamed speech was interrupted by wake word")

        return completed, stream.full_text

    def on_wake(self) -> None:
        """Handle a single wake-word activation (crash-safe wrapper)."""
        try:
            self._on_wake_inner()
        except Exception as exc:
            logger.error("on_wake failed: %s", exc, exc_info=True)
            self._speak_error("Sorry, something went wrong. I'm still here though.")
            self._set_idle()

    def _on_wake_inner(self) -> None:
        """Handle a single wake-word activation (actual logic)."""
        if not self._running:
            return

        t_wake = time.monotonic()
        logger.info("Wake word activated — recording …")

        # Mark activity for KV cache timer (don't flush mid-conversation)
        self._kv_timer.mark_active()

        # Wake vision from sleep if active
        if self.analysis_mode and self.analysis_mode.sleeping:
            self.analysis_mode.wake()

        # Show listening state
        if self._dock_glow:
            self._dock_glow.set_state("listening")
        if self._overlay:
            self._overlay.set_status("listening")
        if self._menubar:
            self._menubar.set_state("listening")

        # 1. Record speech
        audio = record_until_silence()
        t_record = time.monotonic()
        logger.info("[TIMING] Record: %.0fms", (t_record - t_wake) * 1000)

        if audio is None or audio.size == 0:
            self._speak_error("Sorry, I didn't catch that.")
            return

        # 2. Transcribe
        text = self.stt.transcribe(audio)
        t_stt = time.monotonic()
        logger.info("[TIMING] STT: %.0fms", (t_stt - t_record) * 1000)

        if not text:
            self._speak_error("Sorry, I couldn't understand what you said.")
            return

        logger.info("User said: %s", text)

        # Push user message to sidebar transcript
        if self._overlay and hasattr(self._overlay, 'push_transcript'):
            self._overlay.push_transcript("user", text)

        # Switch to processing state
        if self._dock_glow:
            self._dock_glow.set_state("processing")
        if self._overlay:
            self._overlay.set_status("processing")
        if self._menubar:
            self._menubar.set_state("processing")

        # 3. Check for built-in commands
        cmd = parse_command(text)
        if cmd.result == CommandResult.CLEAR_HISTORY:
            self.memory_router.clear_history()
            if cmd.message:
                self.tts.speak(cmd.message)
            self._set_idle()
            return
        if cmd.result == CommandResult.PAUSE:
            if cmd.message:
                self.tts.speak(cmd.message)
            self._set_idle()
            return
        if cmd.result == CommandResult.RESUME:
            if cmd.message:
                self.tts.speak(cmd.message)
            self._set_idle()
            return
        if cmd.result == CommandResult.HANDLED:
            if cmd.message:
                self.tts.speak(cmd.message)
            self._set_idle()
            return

        # Sidebar show/hide
        if cmd.result == CommandResult.SIDEBAR_SHOW:
            if self._overlay and hasattr(self._overlay, '_slide_in'):
                self._overlay._slide_in()
            if cmd.message:
                self.tts.speak(cmd.message)
            self._set_idle()
            return
        if cmd.result == CommandResult.SIDEBAR_HIDE:
            if self._overlay and hasattr(self._overlay, '_slide_out'):
                self._overlay._slide_out()
            if cmd.message:
                self.tts.speak(cmd.message)
            self._set_idle()
            return

        # Shutdown
        if cmd.result == CommandResult.SHUTDOWN:
            if cmd.message:
                self.tts.speak(cmd.message)
            logger.info("Shutdown command received — terminating Nova")
            self.stop()
            import os
            os._exit(0)

        # Vision: contextual screen question
        if cmd.result == CommandResult.VISION_CONTEXTUAL:
            if not self._vision_enabled:
                self.tts.speak("Vision is disabled. Start with --vision flag to enable screen analysis.")
                self._set_idle()
                return
            self._handle_vision_contextual(text)
            return

        # Vision: one-shot screen analysis
        if cmd.result == CommandResult.VISION_ANALYZE:
            if not self._vision_enabled:
                self.tts.speak("Vision is disabled. Start with --vision flag to enable screen analysis.")
                self._set_idle()
                return
            if cmd.message:
                self.tts.speak(cmd.message)
            self._handle_vision_once()
            return

        # Vision: toggle analysis mode
        if cmd.result == CommandResult.VISION_TOGGLE:
            if not self._vision_enabled:
                self.tts.speak("Vision is disabled. Start with --vision flag to enable screen analysis.")
                self._set_idle()
                return
            self._handle_vision_toggle()
            return

        # 4. Check if user is implicitly asking about their screen
        if self._vision_enabled and self._is_screen_related(text):
            self._handle_vision_contextual(text)
            return

        # 5. Query LLM (streaming when available)
        self._kv_timer.mark_active()

        if hasattr(self.llm, 'chat_stream'):
            # --- Streaming path: overlap LLM inference with TTS ---
            try:
                token_gen = self.memory_router.chat_stream(text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("chat_stream init failed, falling back: %s", exc)
                token_gen = None

            if token_gen is not None:
                if self._dock_glow:
                    self._dock_glow.set_state("speaking")
                if self._overlay:
                    self._overlay.set_status("speaking")
                if self._menubar:
                    self._menubar.set_state("speaking")

                completed, reply = self._speak_streamed_interruptible(token_gen)
                t_done = time.monotonic()
                logger.info("[TIMING] LLM+TTS (streamed): %.0fms | Total: %.0fms",
                            (t_done - t_stt) * 1000, (t_done - t_wake) * 1000)

                if not reply:
                    self._speak_error("Sorry, I'm having trouble thinking right now.")
                    return

                logger.info("Nova says: %s", reply[:120])

                # Push agent reply to sidebar transcript
                if self._overlay and hasattr(self._overlay, 'push_transcript'):
                    self._overlay.push_transcript("agent", reply)

                self._set_idle()

                # If interrupted, immediately re-enter the listen flow
                if not completed:
                    logger.info("Re-entering listen mode after interrupt")
                    self.on_wake()
                return

        # --- Fallback: non-streaming path (original behaviour) ---
        reply = self.memory_router.chat(text)
        t_llm = time.monotonic()
        logger.info("[TIMING] LLM: %.0fms", (t_llm - t_stt) * 1000)

        if not reply:
            self._speak_error("Sorry, I'm having trouble thinking right now.")
            return

        # 6. Speak response (interruptible)
        logger.info("Nova says: %s", reply[:120])

        # Push agent reply to sidebar transcript
        if self._overlay and hasattr(self._overlay, 'push_transcript'):
            self._overlay.push_transcript("agent", reply)

        if self._dock_glow:
            self._dock_glow.set_state("speaking")
        if self._overlay:
            self._overlay.set_status("speaking")
        if self._menubar:
            self._menubar.set_state("speaking")

        completed = self._speak_interruptible(reply)
        t_tts = time.monotonic()
        logger.info("[TIMING] TTS: %.0fms | Total: %.0fms",
                    (t_tts - t_llm) * 1000, (t_tts - t_wake) * 1000)

        self._set_idle()

        # If interrupted, immediately re-enter the listen flow
        if not completed:
            logger.info("Re-entering listen mode after interrupt")
            self.on_wake()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_idle(self) -> None:
        """Reset all UI indicators to idle state."""
        if self._dock_glow:
            self._dock_glow.set_state("idle")
        if self._overlay:
            self._overlay.set_status("idle")
        if self._menubar:
            self._menubar.set_state("idle")

    def set_overlay(self, overlay) -> None:
        """Attach an overlay UI for vision results."""
        self._overlay = overlay

    def set_dock_glow(self, glow) -> None:
        """Attach a dock glow indicator."""
        self._dock_glow = glow

    def set_menubar(self, menubar) -> None:
        """Attach a menu bar controller."""
        self._menubar = menubar

    def _handle_vision_once(self) -> None:
        """Capture and analyze the screen once, speak the result.

        Auto-detects the active app and selects the best prompt.
        Saves results to history with category tags.
        """
        logger.info("Vision: one-shot screen analysis")
        if self._overlay:
            self._overlay.set_status("analyzing")
        if self._menubar:
            self._menubar.set_state("analyzing")

        # Detect active app for smart prompt selection
        app_name = get_active_app()
        prompt = select_prompt_for_app(app_name)

        # analyze_once will also detect app if no prompt given,
        # but we pass it explicitly so we can use app_name for history/category
        result = self.analysis_mode.analyze_once(prompt=prompt)

        self._set_idle()

        if result:
            # Categorize the suggestion
            category = categorize_suggestion(result.analysis, app_name)
            tagged_analysis = f"{category} {result.analysis}"

            # Save to history
            frame_bytes = getattr(result, 'frame_bytes', None)
            _save_vision_history(
                result_text=result.analysis,
                app_name=app_name,
                prompt_used=prompt,
                screenshot_bytes=frame_bytes,
                max_history=config.VISION_HISTORY_SIZE,
            )

            # Inject vision result into LLM history for follow-up questions
            self.memory_router.inject_context(
                "assistant",
                f"[Screen Analysis] {tagged_analysis}",
            )
            self.tts.speak(result.analysis)
            if self._overlay:
                self._overlay.push_analysis(tagged_analysis, result.elapsed_ms)
        else:
            self._speak_error("Sorry, I couldn't analyze the screen right now.")

    def _handle_vision_contextual(self, user_text: str) -> None:
        """Capture screen and analyze with the user's specific question as context.

        Uses the improved contextual prompt template and saves to history.
        """
        logger.info("Vision: contextual analysis — %s", user_text[:80])
        if self._overlay:
            self._overlay.set_status("analyzing")
        if self._menubar:
            self._menubar.set_state("analyzing")

        # Detect active app for context
        app_name = get_active_app()

        # Use the improved contextual prompt (analyze_once handles it via
        # build_contextual_prompt in vision.py, but we pass it explicitly here)
        from src.vision_prompts import build_contextual_prompt
        prompt = build_contextual_prompt(user_text)
        result = self.analysis_mode.analyze_once(prompt=prompt)

        self._set_idle()

        if result:
            # Categorize the result
            category = categorize_suggestion(result.analysis, app_name)
            tagged_analysis = f"{category} {result.analysis}"

            # Save to history
            frame_bytes = getattr(result, 'frame_bytes', None)
            _save_vision_history(
                result_text=result.analysis,
                app_name=app_name,
                prompt_used=prompt,
                screenshot_bytes=frame_bytes,
                max_history=config.VISION_HISTORY_SIZE,
            )

            # Inject both user question and vision answer into LLM history
            self.memory_router.inject_context("user", user_text)
            self.memory_router.inject_context(
                "assistant",
                f"[Screen Analysis] {tagged_analysis}",
            )

            # Push to sidebar transcript
            if self._overlay and hasattr(self._overlay, 'push_transcript'):
                self._overlay.push_transcript("agent", tagged_analysis)

            logger.info("Nova (vision) says: %s", result.analysis[:120])
            if self._dock_glow:
                self._dock_glow.set_state("speaking")
            if self._overlay:
                self._overlay.set_status("speaking")
                self._overlay.push_analysis(tagged_analysis, result.elapsed_ms)
            if self._menubar:
                self._menubar.set_state("speaking")
            self.tts.speak(result.analysis)
            self._set_idle()
        else:
            self._speak_error("Sorry, I couldn't analyze the screen right now.")

    @staticmethod
    def _is_screen_related(text: str) -> bool:
        """Check if user text implicitly refers to their screen content.

        Only triggers when the text looks like a *question* about screen content,
        not a statement (e.g., 'my screen is broken' should NOT match).
        """
        import re as _re
        # Must contain a screen reference AND look like a question/request
        _HAS_SCREEN_REF = _re.compile(
            r"\b(?:on\s+(?:my\s+)?(?:the\s+)?screen"
            r"|(?:my\s+)?(?:display|monitor)"
            r"|looking\s+at)",
            _re.IGNORECASE,
        )
        _IS_QUESTION_OR_REQUEST = _re.compile(
            r"(?:^(?:what|how|why|can\s+you|could\s+you|help|explain|tell\s+me|summarize|describe)"
            r"|\?$)",
            _re.IGNORECASE,
        )
        return bool(_HAS_SCREEN_REF.search(text) and _IS_QUESTION_OR_REQUEST.search(text))

    def _handle_vision_toggle(self) -> None:
        """Toggle continuous analysis mode."""
        new_state = self.analysis_mode.toggle()
        if new_state:
            msg = "Analysis mode activated. I'll monitor your screen for changes."
            if self._overlay:
                self._overlay.set_status("analyzing")
            if self._menubar:
                self._menubar.set_state("analyzing")
                self._menubar.set_vision_active(True)
        else:
            msg = "Analysis mode deactivated."
            self._set_idle()
            if self._menubar:
                self._menubar.set_vision_active(False)

        logger.info("Vision: analysis mode %s", "ON" if new_state else "OFF")
        self.tts.speak(msg)

    def analyze_screen_for_chat(self, question: str):
        """Capture the screen and stream vision model tokens for a chat question.

        Yields string tokens as the vision model generates them.
        Callable from the chat handler to route screen-related questions
        through vision instead of the text LLM.
        """
        if not self._vision_enabled or not self.vision_client:
            yield "Vision is not enabled. Start with --vision flag."
            return

        from src.vision import capture_screen

        frame = capture_screen(
            monitor=getattr(config, 'VISION_MONITOR', 0),
            scale=getattr(config, 'VISION_SCALE', 0.5),
        )
        if not frame:
            yield "Sorry, I couldn't capture the screen right now."
            return

        # Update the vision thumbnail in sidebar if available
        if self._overlay and hasattr(self._overlay, 'set_vision_thumbnail'):
            try:
                from PyQt6.QtGui import QPixmap, QImage
                qimg = QImage.fromData(frame)
                if not qimg.isNull():
                    pixmap = QPixmap.fromImage(qimg)
                    self._overlay.set_vision_thumbnail(pixmap)
            except Exception:
                pass  # Non-critical

        # Stream tokens from the vision model
        got_tokens = False
        for token in self.vision_client.analyze_with_question_stream(frame, question):
            got_tokens = True
            yield token

        if not got_tokens:
            yield "Sorry, I couldn't analyze the screen right now."

    def _on_vision_result(self, result: VisionResult) -> None:
        """Callback for continuous analysis results.

        Pushes meaningful insights to the sidebar chat area (not just
        the analysis card), filtering out trivial observations.
        """
        if self._overlay:
            self._overlay.push_analysis(result.analysis, result.elapsed_ms)
            # Also push to chat area if the insight seems meaningful
            # Filter out very short/trivial responses
            if result.analysis and len(result.analysis) > 20:
                self._overlay.push_transcript(
                    "agent", f"🔍 {result.analysis}"
                )

    def _speak_error(self, message: str) -> None:
        """Speak an error message to the user."""
        logger.warning(message)
        if self._dock_glow:
            self._dock_glow.set_state("error")
        if self._menubar:
            self._menubar.set_state("error")
        try:
            self.tts.speak(message)
        except Exception:
            pass
        self._set_idle()

    # ------------------------------------------------------------------
    # Watchdog / Crash Recovery
    # ------------------------------------------------------------------

    def _check_ollama_health(self) -> bool:
        """Ping Ollama and update ``_ollama_healthy`` flag.

        Returns ``True`` if Ollama responded successfully.
        """
        try:
            resp = requests.get(
                f"{config.OLLAMA_BASE_URL}/api/tags",
                timeout=3,
            )
            healthy = resp.status_code == 200
        except Exception:  # noqa: BLE001
            healthy = False

        was_healthy = self._ollama_healthy
        self._ollama_healthy = healthy

        if not healthy and was_healthy:
            logger.warning("Ollama appears down")
        elif healthy and not was_healthy:
            logger.info("Ollama is back online")

        return healthy

    def _can_restart(self) -> bool:
        """Check whether we're within the restart attempt budget.

        Returns ``False`` (and logs a critical message) when
        ``MAX_RESTART_ATTEMPTS`` have been exhausted within the
        ``RESTART_COOLDOWN`` window.
        """
        now = time.monotonic()
        # Purge timestamps older than the cooldown window
        while self._restart_timestamps and (now - self._restart_timestamps[0]) > config.RESTART_COOLDOWN:
            self._restart_timestamps.popleft()

        if len(self._restart_timestamps) >= config.MAX_RESTART_ATTEMPTS:
            logger.critical(
                "Exceeded %d restart attempts within %ds — giving up",
                config.MAX_RESTART_ATTEMPTS,
                config.RESTART_COOLDOWN,
            )
            return False

        self._restart_timestamps.append(now)
        return True

    def _watchdog(self) -> None:
        """Monitor pipeline health and auto-restart on failure.

        Runs as a daemon thread while ``_running`` is ``True``.
        """
        logger.info("Watchdog thread active")
        while self._running:
            time.sleep(config.WATCHDOG_INTERVAL)
            if not self._running:
                break

            # --- Check wake-word detector health ---
            if self.detector is not None and not self.detector._running:
                logger.warning("Wake word detector died — attempting restart …")
                if self._can_restart():
                    try:
                        self.detector.stop()
                        self.detector = WakeWordDetector(
                            on_wake=self.on_wake,
                            wake_word=self._wake_word,
                        )
                        self.detector.start()
                        logger.info("Wake word detector restarted successfully")
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Failed to restart wake word detector: %s", exc)

            # --- Check Ollama health ---
            self._check_ollama_health()

        logger.info("Watchdog thread exiting")


# Backward compat aliases
EPAgentPipeline = NovaPipeline  # Legacy alias
JarvisPipeline = NovaPipeline  # Legacy alias
