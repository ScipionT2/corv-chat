"""Pydantic models for the agent registry."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """Configuration for a single Nova agent."""

    id: str
    """Unique slug, e.g. ``"nova-coder"``."""

    name: str
    """Display name, e.g. ``"Nova Coder"``."""

    system_prompt: str
    """Custom system prompt for this agent."""

    model: str = "qwen2.5:3b"
    """Ollama model name."""

    parent_id: Optional[str] = None
    """Parent agent id (``None`` = top-level)."""

    skills: list[str] = Field(default_factory=list)
    """List of skill ids this agent can use."""

    created_at: datetime = Field(default_factory=datetime.now)
    """Timestamp when the agent was created."""

    enabled: bool = True
    """Whether this agent is currently enabled."""
