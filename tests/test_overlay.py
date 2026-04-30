"""Tests for the overlay module (without requiring PyQt6)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.overlay import is_available, create_overlay


class TestOverlayAvailability:
    def test_is_available_returns_bool(self):
        result = is_available()
        assert isinstance(result, bool)

    def test_create_overlay_without_pyqt(self):
        """If PyQt6 is missing, create_overlay should return None gracefully."""
        if not is_available():
            result = create_overlay()
            assert result is None

    def test_create_overlay_callable(self):
        """create_overlay should always be callable regardless of PyQt6."""
        assert callable(create_overlay)
