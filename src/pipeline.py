"""
EP Agent Pipeline — orchestrates the full voice-interaction loop.

Wake word → Record → Transcribe → LLM → Speak

Includes KV cache management and event-driven vision integration.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import config
from src.commands import CommandResult, parse_command
from src.hybrid_llm import HybridLLMClient
from src.llm import OllamaClient
from src.recorder import record_until_silence
from src.resource_manager import KVCacheTimer
from src.stt import SpeechToText
from src.tts import TextToSpeech
from src.vision import AnalysisMode, VisionClient, VisionResult
from src.wake_word import WakeWordDetector

logger = logging.getLogger(__name__)


class EPAgentPipeline:
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

        # Hybrid LLM: auto-switch between cloud (OpenAI) and local (Ollama)
        if hybrid_mode and not config.OFFLINE_MODE:
            self.llm = HybridLLMClient(
                ollama_model=ollama_model,
                on_mode_change=self._on_llm_mode_change,
            )
        else:
            self.llm = OllamaClient(model=ollama_model)

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
        logger.info("Starting EP Agent pipeline …")

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

        self._running = True
        self._stop_event.clear()

        if self._menubar:
            self._menubar.set_pipeline_running(True)

        logger.info("EP Agent pipeline is running — say '%s' to begin", self._wake_word)

    def stop(self) -> None:
        """Cleanly shut everything down."""
        logger.info("Stopping EP Agent pipeline …")
        self._running = False
        self._stop_event.set()
        if self.detector is not None:
            self.detector.stop()
        if self.analysis_mode is not None:
            self.analysis_mode.stop()
        self._kv_timer.stop()

        # Stop hybrid LLM heartbeat
        if hasattr(self.llm, 'stop'):
            self.llm.stop()

        if self._menubar:
            self._menubar.set_pipeline_running(False)

        logger.info("EP Agent pipeline stopped")

    def wait(self) -> None:
        """Block until :meth:`stop` is called."""
        self._stop_event.wait()

    # ------------------------------------------------------------------
    # Core interaction
    # ------------------------------------------------------------------

    def on_wake(self) -> None:
        """Handle a single wake-word activation."""
        if not self._running:
            return

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
        if audio is None or audio.size == 0:
            self._speak_error("Sorry, I didn't catch that.")
            return

        # 2. Transcribe
        text = self.stt.transcribe(audio)
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
            self.llm.clear_history()
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
            logger.info("Shutdown command received — terminating EP Agent")
            self.stop()
            import os
            os._exit(0)

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

        # 4. Query LLM
        self._kv_timer.mark_active()
        reply = self.llm.chat(text)
        if not reply:
            self._speak_error("Sorry, I'm having trouble thinking right now.")
            return

        # 5. Speak response
        logger.info("EP Agent says: %s", reply[:120])

        # Push agent reply to sidebar transcript
        if self._overlay and hasattr(self._overlay, 'push_transcript'):
            self._overlay.push_transcript("agent", reply)

        if self._dock_glow:
            self._dock_glow.set_state("speaking")
        if self._overlay:
            self._overlay.set_status("speaking")
        if self._menubar:
            self._menubar.set_state("speaking")
        self.tts.speak(reply)
        self._set_idle()

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
        """Capture and analyze the screen once, speak the result."""
        logger.info("Vision: one-shot screen analysis")
        if self._overlay:
            self._overlay.set_status("analyzing")
        if self._menubar:
            self._menubar.set_state("analyzing")

        result = self.analysis_mode.analyze_once(
            prompt="What do you see on this screen? Describe the key content and suggest what the user should do next. Be concise."
        )

        self._set_idle()

        if result:
            self.tts.speak(result.analysis)
            if self._overlay:
                self._overlay.push_analysis(result.analysis, result.elapsed_ms)
        else:
            self._speak_error("Sorry, I couldn't analyze the screen right now.")

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

    def _on_vision_result(self, result: VisionResult) -> None:
        """Callback for continuous analysis results."""
        if self._overlay:
            self._overlay.push_analysis(result.analysis, result.elapsed_ms)

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


# Backward compat alias
JarvisPipeline = EPAgentPipeline
