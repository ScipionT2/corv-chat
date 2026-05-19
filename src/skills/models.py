"""Pydantic models for the skill system."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Skill(BaseModel):
    """Metadata for a loaded Nova skill."""

    id: str
    """Derived from filename (e.g. ``"calculator"``)."""

    name: str
    """Display name."""

    description: str
    """Parsed from docstring or markdown header."""

    source_path: str
    """Path to the ``.py`` or ``.md`` file."""

    skill_type: str
    """``"python"`` or ``"markdown"``."""

    loaded_at: datetime = Field(default_factory=datetime.now)
    """Timestamp when this skill was loaded."""

    enabled: bool = True
    """Whether this skill is currently enabled."""
