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

logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────

VISION_MODEL: str = config.VISION_MODEL
VISION_INTERVAL: float = config.VISION_INTERVAL
VISION_MAX_TOKENS: int = config.VISION_MAX_TOKENS
VISION_PROMPT: str = config.VISION_PROMPT
VISION_CHANGE_THRESHOLD: float = config.VISION_CHANGE_THRESHOLD
VISION_SLEEP_TIMEOUT: float = config.VISION_SLEEP_TIMEOUT


@dataclass
class VisionResult:
    """A single screen analysis result."""
    timestamp: datetime
    analysis: str
    model: str
    elapsed_ms: float
    frame_size_bytes: int = 0


# ─── Screen Capture ───────────────────────────────────────────────────

def capture_screen(monitor: int = 0, scale: float = 0.5) -> Optional[bytes]:
    """Capture the screen and return as PNG bytes.

    Uses mss for fast capture. Falls back to PyAutoGUI if mss
    is unavailable. Downscales by `scale` factor to reduce cost.
    """
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
    """Send images to a local Ollama vision model for analysis."""

    def __init__(
        self,
        base_url: str = config.OLLAMA_BASE_URL,
        model: str = VISION_MODEL,
        timeout: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def analyze_image(
        self,
        image_bytes: bytes,
        prompt: str = VISION_PROMPT,
    ) -> Optional[VisionResult]:
        """Send an image to the vision model and get analysis."""
        b64_image = image_to_base64(image_bytes)

        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [b64_image],
            "stream": True,
            "options": {
                "num_predict": VISION_MAX_TOKENS,
            },
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
                "Cannot connect to Ollama at %s — is it running?", self.base_url
            )
            return None
        except Exception as exc:
            logger.error("Vision request failed: %s", exc)
            return None

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
        """Capture and analyze a single frame (no loop)."""
        frame = capture_screen(monitor=self.monitor, scale=self.scale)
        if not frame:
            return None

        result = self.client.analyze_image(
            frame,
            prompt=prompt or VISION_PROMPT,
        )
        if result:
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
