"""
Ollama Manager — auto-start, health checks, model pre-warming.

Ensures Ollama is running before any vision/LLM call, pulls missing models,
and provides health dashboard data for the UI.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

# Singleton instance
_manager: Optional["OllamaManager"] = None


def get_manager() -> "OllamaManager":
    """Get or create the singleton OllamaManager instance."""
    global _manager
    if _manager is None:
        _manager = OllamaManager()
    return _manager


class OllamaManager:
    """Manages the Ollama process lifecycle, model availability, and health."""

    def __init__(
        self,
        base_url: str = config.OLLAMA_BASE_URL,
        vision_model: str = config.VISION_MODEL,
        chat_model: str = config.OLLAMA_MODEL,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.vision_model = vision_model
        self.chat_model = chat_model
        self._ollama_process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Core: Check / Start Ollama
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        """Check if Ollama is reachable."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def ensure_running(self, timeout: float = 10.0) -> bool:
        """Ensure Ollama is running. Start it if not. Returns True if ready.

        Thread-safe — only one start attempt at a time.
        """
        if self.is_running():
            return True

        with self._lock:
            # Double-check after acquiring lock
            if self.is_running():
                return True

            logger.info("Ollama not running — attempting to start...")
            return self._start_ollama(timeout)

    def _start_ollama(self, timeout: float) -> bool:
        """Spawn `ollama serve` as a background process and wait for readiness."""
        ollama_bin = shutil.which("ollama")
        if not ollama_bin:
            logger.error("Ollama binary not found in PATH — cannot auto-start")
            return False

        try:
            # Spawn ollama serve with output suppressed
            self._ollama_process = subprocess.Popen(
                [ollama_bin, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,  # Detach from parent
            )
            logger.info("Spawned 'ollama serve' (PID %d)", self._ollama_process.pid)
        except Exception as exc:
            logger.error("Failed to spawn ollama serve: %s", exc)
            return False

        # Poll for readiness
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_running():
                logger.info("Ollama is ready (took %.1fs)", timeout - (deadline - time.monotonic()))
                return True
            time.sleep(0.5)

        logger.error("Ollama did not become ready within %.0fs", timeout)
        return False

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------

    def is_model_available(self, model: str) -> bool:
        """Check if a specific model is pulled in Ollama."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            return any(model in m.get("name", "") for m in models)
        except Exception:
            return False

    def pull_model(self, model: str) -> bool:
        """Pull a model from Ollama registry. Blocking call.

        Returns True if pull succeeded.
        """
        logger.info("Pulling model '%s' — this may take a while...", model)
        try:
            resp = requests.post(
                f"{self.base_url}/api/pull",
                json={"name": model, "stream": False},
                timeout=600,  # Models can be large
            )
            if resp.status_code == 200:
                logger.info("Model '%s' pulled successfully", model)
                return True
            else:
                logger.error("Failed to pull model '%s': %s", model, resp.text)
                return False
        except Exception as exc:
            logger.error("Error pulling model '%s': %s", model, exc)
            return False

    def pull_model_background(self, model: str) -> threading.Thread:
        """Pull a model in a background thread. Returns the thread."""
        t = threading.Thread(
            target=self.pull_model,
            args=(model,),
            name=f"ollama-pull-{model}",
            daemon=True,
        )
        t.start()
        return t

    def get_loaded_models(self) -> list[str]:
        """Get list of models currently loaded in Ollama's memory."""
        try:
            resp = requests.get(f"{self.base_url}/api/ps", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            return [m.get("name", "") for m in models]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Model pre-warming
    # ------------------------------------------------------------------

    def warmup_models(self) -> None:
        """Pre-warm both vision and chat models in a background thread.

        Sends a minimal request to each model with keep_alive=10m to load
        them into memory without blocking the UI.
        """
        t = threading.Thread(
            target=self._warmup_models_impl,
            name="ollama-warmup",
            daemon=True,
        )
        t.start()

    def _warmup_models_impl(self) -> None:
        """Internal warmup implementation."""
        for model in [self.chat_model, self.vision_model]:
            if not self.is_model_available(model):
                logger.warning("Model '%s' not available — skipping warmup", model)
                continue
            try:
                logger.info("Warming up model '%s'...", model)
                resp = requests.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": model,
                        "prompt": "",
                        "keep_alive": "10m",
                        "stream": False,
                    },
                    timeout=60,
                )
                if resp.status_code == 200:
                    logger.info("Model '%s' warmed up successfully", model)
                else:
                    logger.warning("Warmup for '%s' returned status %d", model, resp.status_code)
            except Exception as exc:
                logger.warning("Warmup failed for '%s': %s", model, exc)

    # ------------------------------------------------------------------
    # Health dashboard
    # ------------------------------------------------------------------

    def get_health_status(self) -> dict:
        """Return a health status dict for the UI dashboard.

        Returns:
            dict with keys:
                - ollama_running: bool
                - models_loaded: list[str] — models currently in memory
                - ram_total_gb: float
                - ram_available_gb: float
                - gpu_available: bool (Metal on macOS, CUDA on Linux/Windows)
                - vision_model_ready: bool
                - chat_model_ready: bool
        """
        running = self.is_running()
        models_loaded = self.get_loaded_models() if running else []

        # RAM info
        ram_total_gb = 0.0
        ram_available_gb = 0.0
        try:
            import psutil
            mem = psutil.virtual_memory()
            ram_total_gb = round(mem.total / (1024 ** 3), 1)
            ram_available_gb = round(mem.available / (1024 ** 3), 1)
        except ImportError:
            # Fallback: macOS sysctl
            try:
                result = subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    capture_output=True, text=True, timeout=3,
                )
                if result.returncode == 0:
                    ram_total_gb = round(int(result.stdout.strip()) / (1024 ** 3), 1)
            except Exception:
                pass

        # GPU detection
        gpu_available = self._detect_gpu()

        # Model readiness (are they pulled?)
        vision_ready = self.is_model_available(self.vision_model) if running else False
        chat_ready = self.is_model_available(self.chat_model) if running else False

        return {
            "ollama_running": running,
            "models_loaded": models_loaded,
            "ram_total_gb": ram_total_gb,
            "ram_available_gb": ram_available_gb,
            "gpu_available": gpu_available,
            "vision_model_ready": vision_ready,
            "chat_model_ready": chat_ready,
        }

    @staticmethod
    def _detect_gpu() -> bool:
        """Detect if Metal (macOS) or CUDA (Linux/Windows) GPU is available."""
        system = platform.system()
        if system == "Darwin":
            # macOS — Apple Silicon always has Metal
            try:
                result = subprocess.run(
                    ["sysctl", "-n", "hw.optional.arm64"],
                    capture_output=True, text=True, timeout=3,
                )
                # Apple Silicon = Metal guaranteed
                if result.returncode == 0 and result.stdout.strip() == "1":
                    return True
                # Intel Macs with discrete GPU also have Metal
                result2 = subprocess.run(
                    ["system_profiler", "SPDisplaysDataType"],
                    capture_output=True, text=True, timeout=5,
                )
                return "Metal" in result2.stdout
            except Exception:
                return False
        elif system == "Linux":
            # Check for NVIDIA GPU
            return shutil.which("nvidia-smi") is not None
        else:
            # Windows — check for nvidia-smi
            return shutil.which("nvidia-smi") is not None
