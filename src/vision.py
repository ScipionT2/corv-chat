"""
Vision module — event-driven screen analysis via Ollama multimodal models.

Uses change-detection (pixel diff hashing) to avoid constant GPU usage.
Only sends frames to the vision model when significant screen changes occur.

Sleep/Wake cycle:
- Active: checks screen every VISION_INTERVAL seconds (lightweight hash compare)
- If change detected (>15% pixel diff): runs model inference
- If no change for VISION_SLEEP_TIMEOUT: enters deep sleep
- Wakes on: voice trigger or scheduled wake-check

Zero cloud dependencies. All processing stays on-device.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import numpy as np
import requests

import config
from src.app_detector import get_active_app, get_window_bounds
from src.vision_prompts import (
    GENERAL_ANALYSIS_PROMPT,
    build_contextual_prompt,
    select_prompt_for_app,
    categorize_suggestion,
)
from src.ollama_manager import get_manager

logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────

VISION_MODEL: str = config.VISION_MODEL
VISION_INTERVAL: float = config.VISION_INTERVAL
VISION_MAX_TOKENS: int = config.VISION_MAX_TOKENS
VISION_PROMPT: str = config.VISION_PROMPT
VISION_CHANGE_THRESHOLD: float = config.VISION_CHANGE_THRESHOLD
VISION_SLEEP_TIMEOUT: float = config.VISION_SLEEP_TIMEOUT
VISION_FAST_MODE: bool = config.VISION_FAST_MODE


# ─── Error types for clear messaging ─────────────────────────────────

class VisionError(Exception):
    """Base class for vision errors with user-friendly messages."""
    def __init__(self, message: str, user_message: str):
        super().__init__(message)
        self.user_message = user_message


class OllamaNotRunningError(VisionError):
    def __init__(self):
        super().__init__(
            "Ollama is not running and could not be started",
            "Ollama is not running. Please start it with 'ollama serve' or install from ollama.com.",
        )


class ModelNotAvailableError(VisionError):
    def __init__(self, model: str):
        super().__init__(
            f"Model '{model}' is not available",
            f"Model '{model}' not available. Pulling it now — this may take a minute...",
        )
        self.model = model


@dataclass
class VisionResult:
    """A single screen analysis result."""
    timestamp: datetime
    analysis: str
    model: str
    elapsed_ms: float
    frame_size_bytes: int = 0


# ─── Screen Capture ───────────────────────────────────────────────────

def capture_screen(monitor: int = 0, scale: float = None) -> Optional[bytes]:
    """Capture the screen and return as PNG bytes.

    Uses mss for fast capture. Falls back to PyAutoGUI if mss
    is unavailable. Downscales by `scale` factor to reduce cost.
    If scale is None, uses 0.3 in fast mode or config.VISION_SCALE otherwise.
    """
    if scale is None:
        scale = 0.3 if VISION_FAST_MODE else config.VISION_SCALE
    try:
        import mss
        from PIL import Image

        with mss.MSS() as sct:
            monitors = sct.monitors
            mon = monitors[min(monitor, len(monitors) - 1)]
            screenshot = sct.grab(mon)

            img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)

            if scale < 1.0:
                new_size = (int(img.width * scale), int(img.height * scale))
                img = img.resize(new_size, Image.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return buf.getvalue()

    except ImportError:
        logger.warning("mss not available, trying PyAutoGUI fallback")
    except Exception as exc:
        logger.warning("mss screen capture failed: %s — trying PyAutoGUI fallback", exc)

    try:
        import pyautogui
        from PIL import Image

        screenshot = pyautogui.screenshot()
        if scale < 1.0:
            new_size = (int(screenshot.width * scale), int(screenshot.height * scale))
            screenshot = screenshot.resize(new_size, Image.LANCZOS)

        buf = io.BytesIO()
        screenshot.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    except ImportError:
        logger.error("Neither mss nor pyautogui available for screen capture")
        return None
    except Exception as exc:
        logger.error("Screen capture failed: %s", exc)
        return None


def capture_screen_array(monitor: int = 0, scale: float = 0.5) -> Optional[np.ndarray]:
    """Capture screen as a numpy array (for fast change detection)."""
    try:
        import mss
        from PIL import Image

        with mss.MSS() as sct:
            monitors = sct.monitors
            mon = monitors[min(monitor, len(monitors) - 1)]
            screenshot = sct.grab(mon)

            img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)

            # Use aggressive downscale for hash comparison (1/4 res)
            hash_scale = scale * 0.5
            new_size = (int(img.width * hash_scale), int(img.height * hash_scale))
            img = img.resize(new_size, Image.LANCZOS)

            return np.array(img, dtype=np.uint8)

    except Exception as exc:
        logger.debug("Screen array capture failed: %s", exc)
        return None


def capture_active_window(scale: float = 0.5) -> Optional[bytes]:
    """Capture only the active window region instead of the full screen.

    Uses AppleScript to get the frontmost window's bounds, then captures
    just that region with mss. Falls back to full screen capture on failure.

    Parameters
    ----------
    scale : float
        Downscale factor for the captured image.

    Returns
    -------
    Optional[bytes]
        PNG image bytes of the active window, or full screen on failure.
    """
    bounds = get_window_bounds()
    if bounds is None:
        logger.debug("Window bounds detection failed, falling back to full screen")
        return capture_screen(monitor=config.VISION_MONITOR, scale=scale)

    x, y, w, h = bounds

    # Sanity check
    if w <= 0 or h <= 0:
        logger.debug("Invalid window bounds (%d, %d, %d, %d), falling back", x, y, w, h)
        return capture_screen(monitor=config.VISION_MONITOR, scale=scale)

    try:
        import mss
        from PIL import Image

        with mss.MSS() as sct:
            monitor_region = {"left": x, "top": y, "width": w, "height": h}
            screenshot = sct.grab(monitor_region)

            img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)

            if scale < 1.0:
                new_size = (int(img.width * scale), int(img.height * scale))
                img = img.resize(new_size, Image.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return buf.getvalue()

    except ImportError:
        logger.debug("mss not available for window capture, falling back")
        return capture_screen(monitor=config.VISION_MONITOR, scale=scale)
    except Exception as exc:
        logger.debug("Window capture failed: %s — falling back to full screen", exc)
        return capture_screen(monitor=config.VISION_MONITOR, scale=scale)


def smart_capture(scale: float = 0.5) -> Optional[bytes]:
    """Capture screen using the best available method.

    If EP_VISION_WINDOW_ONLY is true, tries window-only capture first.
    Falls back to full screen if window capture fails.
    """
    if config.VISION_WINDOW_ONLY:
        return capture_active_window(scale=scale)
    return capture_screen(monitor=config.VISION_MONITOR, scale=scale)


def compute_frame_diff(frame_a: np.ndarray, frame_b: np.ndarray) -> float:
    """Compute the normalized pixel difference ratio between two frames.

    Returns a value between 0.0 (identical) and 1.0 (completely different).
    Uses mean absolute difference normalized to [0, 1].
    """
    if frame_a.shape != frame_b.shape:
        return 1.0  # Shape mismatch = consider it changed

    diff = np.abs(frame_a.astype(np.int16) - frame_b.astype(np.int16))
    # Normalize: max possible diff per pixel per channel is 255
    ratio = diff.mean() / 255.0
    return float(ratio)


def image_to_base64(png_bytes: bytes) -> str:
    """Encode PNG bytes to base64 string for Ollama API."""
    return base64.b64encode(png_bytes).decode("utf-8")


# ─── Ollama Vision Client ────────────────────────────────────────────

class VisionClient:
    """Send images to a local Ollama vision model for analysis.

    Includes auto-start of Ollama, retry logic, and clear error reporting.
    """

    def __init__(
        self,
        base_url: str = config.OLLAMA_BASE_URL,
        model: str = VISION_MODEL,
        timeout: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._manager = get_manager()

    def _get_max_tokens(self) -> int:
        """Get max tokens based on fast mode setting."""
        if VISION_FAST_MODE:
            return 256
        return VISION_MAX_TOKENS

    def _build_options(self) -> dict:
        """Build options dict, respecting fast mode."""
        opts = {"num_predict": self._get_max_tokens()}
        return opts

    def _ensure_ready(self) -> Optional[str]:
        """Ensure Ollama is running and model is available.

        Returns None if ready, or an error message string if not.
        """
        # Check/start Ollama
        if not self._manager.ensure_running(timeout=10.0):
            return (
                "Ollama is not running and could not be started automatically. "
                "Please run 'ollama serve' manually."
            )

        # Check model availability
        if not self._manager.is_model_available(self.model):
            logger.warning("Model '%s' not found — attempting pull...", self.model)
            # Start pull in background and inform caller
            self._manager.pull_model_background(self.model)
            return (
                f"Model '{self.model}' is not available. "
                f"Pulling it now — this may take a few minutes. Please try again shortly."
            )

        return None

    def analyze_image(
        self,
        image_bytes: bytes,
        prompt: str = VISION_PROMPT,
        _retry: bool = True,
    ) -> Optional[VisionResult]:
        """Send an image to the vision model and get analysis.

        Includes retry logic:
        - Connection refused → try auto-start Ollama, retry once
        - Model not found → pull in background, inform user
        - Other failures → wait 1s, retry once
        """
        # Pre-flight: ensure Ollama + model ready
        error_msg = self._ensure_ready()
        if error_msg:
            logger.error(error_msg)
            return VisionResult(
                timestamp=datetime.now(),
                analysis=error_msg,
                model=self.model,
                elapsed_ms=0,
                frame_size_bytes=len(image_bytes),
            )

        b64_image = image_to_base64(image_bytes)

        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [b64_image],
            "stream": True,
            "options": self._build_options(),
        }

        start = time.monotonic()
        logger.debug("POST %s model=%s image_size=%d", url, self.model, len(image_bytes))

        try:
            resp = requests.post(url, json=payload, timeout=self.timeout, stream=True)
            resp.raise_for_status()

            parts: list[str] = []
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    if token:
                        parts.append(token)
                    if chunk.get("done"):
                        break
                except json.JSONDecodeError:
                    continue

            elapsed = (time.monotonic() - start) * 1000
            analysis = "".join(parts).strip()

            if not analysis:
                logger.warning("Vision model returned empty response")
                return None

            result = VisionResult(
                timestamp=datetime.now(),
                analysis=analysis,
                model=self.model,
                elapsed_ms=round(elapsed, 1),
                frame_size_bytes=len(image_bytes),
            )
            logger.info(
                "Vision analysis complete (%dms, %d bytes): %s",
                int(elapsed), len(image_bytes), analysis[:100],
            )
            return result

        except requests.ConnectionError:
            logger.error(
                "Cannot connect to Ollama at %s — attempting recovery...", self.base_url
            )
            if _retry:
                # Try to start Ollama and retry once
                if self._manager.ensure_running(timeout=10.0):
                    time.sleep(1)
                    return self.analyze_image(image_bytes, prompt, _retry=False)
            return VisionResult(
                timestamp=datetime.now(),
                analysis="Cannot connect to Ollama. Please ensure it's installed and running.",
                model=self.model,
                elapsed_ms=0,
                frame_size_bytes=len(image_bytes),
            )

        except requests.HTTPError as exc:
            if "not found" in str(exc).lower() or (hasattr(exc, 'response') and exc.response and exc.response.status_code == 404):
                logger.error("Model '%s' not found — pulling...", self.model)
                self._manager.pull_model_background(self.model)
                return VisionResult(
                    timestamp=datetime.now(),
                    analysis=f"Model '{self.model}' not found. Pulling it now — please try again in a minute.",
                    model=self.model,
                    elapsed_ms=0,
                    frame_size_bytes=len(image_bytes),
                )
            logger.error("Vision request HTTP error: %s", exc)
            if _retry:
                time.sleep(1)
                return self.analyze_image(image_bytes, prompt, _retry=False)
            return None

        except Exception as exc:
            logger.error("Vision request failed: %s", exc)
            if _retry:
                time.sleep(1)
                return self.analyze_image(image_bytes, prompt, _retry=False)
            return None

    def analyze_image_stream(
        self,
        image_bytes: bytes,
        prompt: str = VISION_PROMPT,
        _retry: bool = True,
    ):
        """Generator that yields tokens as they stream from the vision model.

        Yields individual string tokens. The caller accumulates them.
        Includes retry logic: on connection error, tries auto-start + retry once.
        On model not found, yields an error message token.
        """
        # Pre-flight: ensure Ollama + model ready
        error_msg = self._ensure_ready()
        if error_msg:
            logger.error(error_msg)
            yield error_msg
            return

        b64_image = image_to_base64(image_bytes)

        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [b64_image],
            "stream": True,
            "options": self._build_options(),
        }

        logger.debug("POST %s model=%s image_size=%d (streaming)", url, self.model, len(image_bytes))

        try:
            resp = requests.post(url, json=payload, timeout=self.timeout, stream=True)
            resp.raise_for_status()

            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    if token:
                        yield token
                    if chunk.get("done"):
                        break
                except json.JSONDecodeError:
                    continue

        except requests.ConnectionError:
            logger.error(
                "Cannot connect to Ollama at %s — attempting recovery...", self.base_url
            )
            if _retry:
                if self._manager.ensure_running(timeout=10.0):
                    time.sleep(1)
                    yield from self.analyze_image_stream(image_bytes, prompt, _retry=False)
                    return
            yield "Cannot connect to Ollama. Please ensure it's installed and running."

        except requests.HTTPError as exc:
            if "not found" in str(exc).lower() or (hasattr(exc, 'response') and exc.response and exc.response.status_code == 404):
                self._manager.pull_model_background(self.model)
                yield f"Model '{self.model}' not found. Pulling it now — please try again in a minute."
                return
            logger.error("Vision stream HTTP error: %s", exc)
            if _retry:
                time.sleep(1)
                yield from self.analyze_image_stream(image_bytes, prompt, _retry=False)
                return
            yield "Vision analysis failed. Please try again."

        except Exception as exc:
            logger.error("Vision stream request failed: %s", exc)
            if _retry:
                time.sleep(1)
                yield from self.analyze_image_stream(image_bytes, prompt, _retry=False)
                return
            yield "Vision analysis failed. Please try again."

    def analyze_with_question(
        self,
        image_bytes: bytes,
        question: str,
    ) -> Optional[VisionResult]:
        """Analyze an image with a user's specific question as context.

        Frames the prompt around the user's question for contextual analysis.
        Uses the improved contextual prompt template.
        """
        prompt = build_contextual_prompt(question)
        return self.analyze_image(image_bytes, prompt=prompt)

    def analyze_with_question_stream(
        self,
        image_bytes: bytes,
        question: str,
    ):
        """Stream tokens for a contextual screen question.

        Yields individual string tokens. Uses the improved contextual prompt.
        """
        prompt = build_contextual_prompt(question)
        yield from self.analyze_image_stream(image_bytes, prompt=prompt)

    def check_model_available(self) -> bool:
        """Check if the vision model is available in Ollama."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            return any(self.model in m.get("name", "") for m in models)
        except Exception:
            return False


# ─── Analysis Mode (Event-Driven Screen Monitoring) ───────────────────

class AnalysisMode:
    """Event-driven screen analysis with change detection.

    Instead of constant polling + model inference, this uses a Sleep/Wake cycle:
    1. Lightweight screen capture + pixel-diff comparison (very cheap)
    2. Only invokes the vision model when change exceeds threshold
    3. Enters deep sleep after no changes for VISION_SLEEP_TIMEOUT
    4. Wakes on voice trigger or scheduled check

    This reduces idle CPU from ~30% to <2%.
    """

    def __init__(
        self,
        on_result: Callable[[VisionResult], None],
        interval: float = VISION_INTERVAL,
        monitor: int = 0,
        scale: float = 0.5,
        vision_client: Optional[VisionClient] = None,
        change_threshold: float = VISION_CHANGE_THRESHOLD,
        sleep_timeout: float = VISION_SLEEP_TIMEOUT,
    ) -> None:
        self.on_result = on_result
        self.interval = interval
        self.monitor = monitor
        self.scale = scale
        self.client = vision_client or VisionClient()
        self.change_threshold = change_threshold
        self.sleep_timeout = sleep_timeout

        self._active = False
        self._sleeping = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._results: list[VisionResult] = []
        self._last_frame: Optional[np.ndarray] = None
        self._last_change_time: float = 0.0
        self._frames_checked: int = 0
        self._frames_analyzed: int = 0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def sleeping(self) -> bool:
        return self._sleeping

    @property
    def results(self) -> list[VisionResult]:
        return list(self._results)

    @property
    def latest(self) -> Optional[VisionResult]:
        return self._results[-1] if self._results else None

    @property
    def stats(self) -> dict:
        """Return efficiency stats."""
        return {
            "frames_checked": self._frames_checked,
            "frames_analyzed": self._frames_analyzed,
            "efficiency": (
                f"{(1 - self._frames_analyzed / max(1, self._frames_checked)) * 100:.0f}% skipped"
            ),
            "sleeping": self._sleeping,
        }

    def toggle(self) -> bool:
        """Toggle analysis mode on/off. Returns new state."""
        if self._active:
            self.stop()
            return False
        else:
            self.start()
            return True

    def wake(self) -> None:
        """Force wake from deep sleep (e.g., on voice trigger)."""
        self._sleeping = False
        self._wake_event.set()
        logger.info("Vision: woken from sleep")

    def start(self) -> None:
        """Start event-driven screen analysis."""
        if self._active:
            return

        self._active = True
        self._sleeping = False
        self._stop_event.clear()
        self._wake_event.clear()
        self._last_frame = None
        self._last_change_time = time.monotonic()

        self._thread = threading.Thread(
            target=self._analysis_loop,
            name="ep-vision-loop",
            daemon=True,
        )
        self._thread.start()
        logger.info("Analysis mode STARTED (event-driven, interval=%.1fs, threshold=%.0f%%)",
                    self.interval, self.change_threshold * 100)

    def stop(self) -> None:
        """Stop screen analysis."""
        if not self._active:
            return

        self._active = False
        self._stop_event.set()
        self._wake_event.set()  # Unblock sleep
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Analysis mode STOPPED (checked=%d, analyzed=%d)",
                    self._frames_checked, self._frames_analyzed)

    def analyze_once(self, prompt: Optional[str] = None) -> Optional[VisionResult]:
        """Capture and analyze a single frame (no loop).

        If no prompt is provided, auto-selects based on active app.
        Uses window-only capture when configured.
        """
        frame = smart_capture(scale=self.scale)
        if not frame:
            return None

        # Auto-select prompt based on active app if none provided
        if prompt is None:
            app_name = get_active_app()
            prompt = select_prompt_for_app(app_name)

        result = self.client.analyze_image(frame, prompt=prompt)
        if result:
            # Store frame bytes for history saving by pipeline
            result.frame_bytes = frame  # type: ignore[attr-defined]
            self._results.append(result)
        return result

    def _analysis_loop(self) -> None:
        """Event-driven loop: capture → hash compare → analyze only if changed.

        Sleep/Wake cycle:
        - Check screen at interval (lightweight pixel diff)
        - Run model ONLY if change > threshold
        - Enter deep sleep if no change for sleep_timeout
        - Wake on voice trigger or scheduled wake-check
        """
        try:
            from src.resource_manager import adaptive_vision_interval
        except ImportError:
            adaptive_vision_interval = None

        while not self._stop_event.is_set():
            # ── Deep Sleep Mode ───────────────────────────────────────
            if self._sleeping:
                logger.debug("Vision: deep sleep — waiting for wake event")
                # Wait for wake event or periodic check (every 10s)
                self._wake_event.wait(timeout=10.0)
                self._wake_event.clear()

                if self._stop_event.is_set():
                    break

                # Quick check if screen changed during sleep
                current_frame = capture_screen_array(self.monitor, self.scale)
                if current_frame is not None and self._last_frame is not None:
                    diff = compute_frame_diff(self._last_frame, current_frame)
                    if diff >= self.change_threshold:
                        self._sleeping = False
                        self._last_change_time = time.monotonic()
                        logger.info("Vision: woke from sleep (screen changed, diff=%.1f%%)", diff * 100)
                    else:
                        continue  # Still sleeping
                else:
                    continue

            # ── Active Mode: Change Detection ─────────────────────────
            try:
                self._frames_checked += 1
                current_frame = capture_screen_array(self.monitor, self.scale)

                if current_frame is None:
                    self._stop_event.wait(timeout=self.interval)
                    continue

                # First frame — just store it
                if self._last_frame is None:
                    self._last_frame = current_frame
                    self._last_change_time = time.monotonic()
                    self._stop_event.wait(timeout=self.interval)
                    continue

                # Compute pixel difference
                diff = compute_frame_diff(self._last_frame, current_frame)
                self._last_frame = current_frame

                if diff >= self.change_threshold:
                    # Significant change detected → run model
                    self._last_change_time = time.monotonic()
                    self._frames_analyzed += 1

                    logger.info("Vision: change detected (%.1f%%) → analyzing", diff * 100)

                    # Capture full-res frame for model
                    full_frame = capture_screen(monitor=self.monitor, scale=self.scale)
                    if full_frame:
                        result = self.client.analyze_image(full_frame)
                        if result:
                            self._results.append(result)
                            if len(self._results) > 50:
                                self._results = self._results[-50:]
                            try:
                                self.on_result(result)
                            except Exception as exc:
                                logger.error("Result callback error: %s", exc)
                else:
                    # No significant change — check if we should sleep
                    idle_time = time.monotonic() - self._last_change_time
                    if idle_time >= self.sleep_timeout:
                        self._sleeping = True
                        logger.info(
                            "Vision: entering deep sleep (no change for %.0fs)",
                            idle_time,
                        )
                        continue

            except Exception as exc:
                logger.error("Analysis loop error: %s", exc)

            # Adaptive throttle
            interval = self.interval
            if adaptive_vision_interval:
                interval = adaptive_vision_interval(self.interval)
            self._stop_event.wait(timeout=interval)
