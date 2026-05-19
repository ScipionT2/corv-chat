"""Thread-safe skill registry with hot-reload support."""

from __future__ import annotations

import logging
import threading
from typing import Optional

from src.skills.loader import SkillLoader
from src.skills.models import Skill

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Thread-safe skill registry with hot-reload support."""

    def __init__(self, skills_dir: str = "~/.nova/skills/") -> None:
        self.loader = SkillLoader(skills_dir)
        self._lock = threading.Lock()
        self._skills: dict[str, Skill] = {}

    def scan_and_load(self) -> None:
        """Scan skills dir and load all found skills."""
        with self._lock:
            discovered = self.loader.scan()
            for sk in discovered:
                # Preserve enabled state from previous load
                prev = self._skills.get(sk.id)
                if prev is not None:
                    sk.enabled = prev.enabled
                self._skills[sk.id] = sk
            logger.info("Loaded %d skill(s)", len(discovered))

    def get(self, skill_id: str) -> Optional[Skill]:
        with self._lock:
            return self._skills.get(skill_id)

    def list_skills(self) -> list[Skill]:
        with self._lock:
            return list(self._skills.values())

    def reload(self) -> None:
        """Re-scan and reload all skills."""
        self.scan_and_load()

    def enable(self, skill_id: str) -> None:
        with self._lock:
            sk = self._skills.get(skill_id)
            if sk:
                sk.enabled = True

    def disable(self, skill_id: str) -> None:
        with self._lock:
            sk = self._skills.get(skill_id)
            if sk:
                sk.enabled = False
