"""Thread-safe agent registry with JSON persistence."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import config
from src.agents.models import AgentConfig

logger = logging.getLogger(__name__)

_DEFAULT_AGENT_ID = "nova-default"


class AgentRegistry:
    """Thread-safe agent registry with JSON persistence.

    Agents are persisted to ``~/.nova/agents.json``.  A default *Nova*
    agent is created automatically on first run.
    """

    def __init__(self, config_path: str = "~/.nova/agents.json") -> None:
        self._path = Path(os.path.expanduser(config_path))
        self._lock = threading.Lock()
        self._agents: dict[str, AgentConfig] = {}
        self._active_id: str = _DEFAULT_AGENT_ID
        self._load()

    # ── CRUD ──────────────────────────────────────────────────────

    def register(self, cfg: AgentConfig) -> None:
        """Register (or overwrite) an agent."""
        with self._lock:
            self._agents[cfg.id] = cfg
            self._save_locked()

    def unregister(self, agent_id: str) -> None:
        """Remove an agent by id."""
        with self._lock:
            if agent_id == _DEFAULT_AGENT_ID:
                raise ValueError("Cannot unregister the default agent")
            self._agents.pop(agent_id, None)
            if self._active_id == agent_id:
                self._active_id = _DEFAULT_AGENT_ID
            self._save_locked()

    def get(self, agent_id: str) -> Optional[AgentConfig]:
        with self._lock:
            return self._agents.get(agent_id)

    def list_agents(self) -> list[AgentConfig]:
        with self._lock:
            return list(self._agents.values())

    def get_children(self, parent_id: str) -> list[AgentConfig]:
        with self._lock:
            return [a for a in self._agents.values() if a.parent_id == parent_id]

    def get_hierarchy(self, agent_id: str) -> list[AgentConfig]:
        """Return breadcrumb chain from root to *agent_id*."""
        with self._lock:
            chain: list[AgentConfig] = []
            current = self._agents.get(agent_id)
            while current:
                chain.append(current)
                if current.parent_id is None:
                    break
                current = self._agents.get(current.parent_id)
            chain.reverse()
            return chain

    # ── Active agent ──────────────────────────────────────────────

    def set_active(self, agent_id: str) -> None:
        with self._lock:
            if agent_id not in self._agents:
                raise ValueError(f"Agent '{agent_id}' not found")
            self._active_id = agent_id
            self._save_locked()

    def get_active(self) -> AgentConfig:
        with self._lock:
            agent = self._agents.get(self._active_id)
            if agent is None:
                # Fallback to default
                agent = self._agents.get(_DEFAULT_AGENT_ID)
            if agent is None:
                # Create default on the fly
                agent = self._make_default()
                self._agents[agent.id] = agent
            return agent

    # ── Persistence ───────────────────────────────────────────────

    def save(self) -> None:
        with self._lock:
            self._save_locked()

    def _save_locked(self) -> None:
        """Persist to JSON (caller must hold lock)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "active_id": self._active_id,
            "agents": [a.model_dump(mode="json") for a in self._agents.values()],
        }
        self._path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def _load(self) -> None:
        """Load from JSON, creating the default agent if needed."""
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._active_id = raw.get("active_id", _DEFAULT_AGENT_ID)
                for entry in raw.get("agents", []):
                    agent = AgentConfig(**entry)
                    self._agents[agent.id] = agent
            except Exception as exc:
                logger.warning("Failed to load agents.json: %s — starting fresh", exc)

        if _DEFAULT_AGENT_ID not in self._agents:
            default = self._make_default()
            self._agents[default.id] = default
            self._save_locked()

    @staticmethod
    def _make_default() -> AgentConfig:
        return AgentConfig(
            id=_DEFAULT_AGENT_ID,
            name="Nova",
            system_prompt=config.LLM_SYSTEM_PROMPT,
            model=config.OLLAMA_MODEL,
        )
