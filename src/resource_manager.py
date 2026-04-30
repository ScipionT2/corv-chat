"""
Resource Manager — CPU/memory throttling, thread limiting, and KV cache control.

Ensures EP Agent runs as a lightweight background daemon:
- Low process priority (never freezes the OS)
- CPU thread cap at 25% of cores
- Periodic KV cache flushing to prevent VRAM leaks
- Adaptive throttling for vision/inference loops
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import threading
import time
from typing import Optional

import config

logger = logging.getLogger(__name__)


# ─── CPU Thread Limiting ──────────────────────────────────────────────

def limit_cpu_threads() -> None:
    """Limit CPU threads to 25% of available cores for all subsystems.

    Must be called BEFORE importing numpy, torch, or loading models.
    Sets OMP_NUM_THREADS, MKL_NUM_THREADS, OLLAMA_NUM_THREAD, and
    OPENBLAS_NUM_THREADS.
    """
    total_cores = os.cpu_count() or 4
    max_threads = max(2, total_cores // 4)  # 25% of cores, minimum 2

    thread_vars = [
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "OLLAMA_NUM_THREAD",
        "NUMEXPR_MAX_THREADS",
    ]

    for var in thread_vars:
        os.environ.setdefault(var, str(max_threads))

    logger.info(
        "CPU thread limit: %d/%d cores (25%%)",
        max_threads, total_cores,
    )


def set_process_priority(priority: str = config.PROCESS_PRIORITY) -> None:
    """Set the current process priority.

    Parameters
    ----------
    priority:
        'low', 'normal', or 'high'.
    """
    nice_map = {"low": 10, "normal": 0, "high": -5}
    nice_value = nice_map.get(priority, 10)

    # Method 1: psutil (cross-platform, best control)
    try:
        import psutil
        p = psutil.Process(os.getpid())

        if platform.system() == "Darwin" or platform.system() == "Linux":
            p.nice(nice_value)
        elif platform.system() == "Windows":
            priority_classes = {
                "low": psutil.IDLE_PRIORITY_CLASS,
                "normal": psutil.NORMAL_PRIORITY_CLASS,
                "high": psutil.HIGH_PRIORITY_CLASS,
            }
            p.nice(priority_classes.get(priority, psutil.IDLE_PRIORITY_CLASS))

        logger.info("Process priority set to '%s' (nice=%d) via psutil", priority, nice_value)
        return
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("psutil priority failed: %s, trying os.nice()", exc)

    # Method 2: os.nice() (Unix only)
    if platform.system() in ("Darwin", "Linux"):
        try:
            os.nice(nice_value)
            logger.info("Process priority set to '%s' (nice=%d) via os.nice()", priority, nice_value)
        except OSError as exc:
            logger.warning("os.nice() failed: %s (may need root for negative values)", exc)
    else:
        logger.warning("Cannot set process priority on this platform without psutil")


def set_ollama_gpu_layers() -> None:
    """Ensure Ollama uses full GPU offload by setting env vars."""
    num_gpu = str(config.OLLAMA_NUM_GPU)
    os.environ.setdefault("OLLAMA_NUM_GPU", num_gpu)
    logger.info("OLLAMA_NUM_GPU=%s (full GPU offload)", num_gpu)


# ─── KV Cache Management ─────────────────────────────────────────────

def flush_kv_cache(base_url: str = config.OLLAMA_BASE_URL, model: str = config.OLLAMA_MODEL) -> bool:
    """Flush Ollama's KV cache by unloading and reloading the model.

    1. Unloads model (keep_alive=0s) → frees all KV cache VRAM
    2. Re-preloads model (keep_alive=10m) → warm for next query

    Returns True on success, False on failure.
    """
    import requests

    url = f"{base_url.rstrip('/')}/api/generate"

    try:
        # Step 1: Unload model (clears KV cache)
        resp = requests.post(url, json={
            "model": model,
            "prompt": "",
            "keep_alive": "0s",
        }, timeout=30)
        resp.raise_for_status()

        # Step 2: Preload model with fresh KV cache
        resp = requests.post(url, json={
            "model": model,
            "prompt": "",
            "keep_alive": "10m",
        }, timeout=60)
        resp.raise_for_status()

        logger.info("KV cache flushed for model '%s'", model)
        return True

    except Exception as exc:
        logger.warning("KV cache flush failed: %s", exc)
        return False


class KVCacheTimer:
    """Background timer that flushes KV cache periodically when idle.

    Only flushes when the pipeline has been idle (no active conversation)
    for at least the configured interval.
    """

    def __init__(
        self,
        interval_minutes: int = config.KV_CACHE_FLUSH_INTERVAL,
        base_url: str = config.OLLAMA_BASE_URL,
        model: str = config.OLLAMA_MODEL,
    ) -> None:
        self._interval = interval_minutes * 60  # seconds
        self._base_url = base_url
        self._model = model
        self._last_activity: float = time.monotonic()
        self._timer: Optional[threading.Timer] = None
        self._running = False

    def start(self) -> None:
        """Start the periodic flush timer."""
        self._running = True
        self._schedule_next()
        logger.info("KV cache timer started (interval=%dm)", self._interval // 60)

    def stop(self) -> None:
        """Stop the timer."""
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def mark_active(self) -> None:
        """Mark that a conversation is happening (delays flush)."""
        self._last_activity = time.monotonic()

    def _schedule_next(self) -> None:
        """Schedule the next flush check."""
        if not self._running:
            return
        self._timer = threading.Timer(self._interval, self._check_and_flush)
        self._timer.daemon = True
        self._timer.start()

    def _check_and_flush(self) -> None:
        """Check if idle long enough, then flush."""
        if not self._running:
            return

        idle_seconds = time.monotonic() - self._last_activity
        if idle_seconds >= self._interval:
            flush_kv_cache(self._base_url, self._model)
        else:
            logger.debug(
                "KV flush skipped — last activity %.0fs ago (need %ds)",
                idle_seconds, self._interval,
            )

        self._schedule_next()


# ─── Adaptive Throttling ─────────────────────────────────────────────

def get_cpu_percent() -> float:
    """Get current process CPU usage (0-100+)."""
    try:
        import psutil
        p = psutil.Process(os.getpid())
        return p.cpu_percent(interval=0.1)
    except (ImportError, Exception):
        return 0.0


def adaptive_vision_interval(base_interval: float) -> float:
    """Return an adjusted vision interval based on current CPU load."""
    cpu = get_cpu_percent()
    if cpu > config.MAX_CPU_PERCENT:
        adjusted = min(base_interval * 2.0, 60.0)
        logger.debug("CPU at %.1f%% > %.1f%% cap, vision interval → %.1fs",
                     cpu, config.MAX_CPU_PERCENT, adjusted)
        return adjusted
    return base_interval


# ─── System Info ─────────────────────────────────────────────────────

def log_system_info() -> None:
    """Log useful system info at startup."""
    logger.info("Platform: %s %s", platform.system(), platform.machine())
    logger.info("Python: %s", sys.version.split()[0])

    # Check for Metal/CUDA
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        logger.info("Hardware: Apple Silicon → Metal GPU acceleration available")
    elif platform.system() == "Linux":
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                logger.info("Hardware: NVIDIA GPU → %s", result.stdout.strip())
            else:
                logger.info("Hardware: No NVIDIA GPU detected, using CPU")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.info("Hardware: No NVIDIA GPU detected, using CPU")

    # Memory
    try:
        import psutil
        mem = psutil.virtual_memory()
        logger.info("RAM: %.1f GB total, %.1f GB available",
                    mem.total / 1e9, mem.available / 1e9)
    except ImportError:
        pass

    # Thread limits
    total_cores = os.cpu_count() or 4
    max_threads = max(1, total_cores // 4)
    logger.info("Thread cap: %d threads (25%% of %d cores)", max_threads, total_cores)
