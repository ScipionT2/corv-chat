"""Skill loader — discovers and loads ``.py`` and ``.md`` skills from a directory."""

from __future__ import annotations

import importlib.util
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.skills.models import Skill

logger = logging.getLogger(__name__)


class SkillLoader:
    """Loads ``.py`` and ``.md`` skills from a directory."""

    def __init__(self, skills_dir: str = "~/.nova/skills/") -> None:
        self.skills_dir = Path(os.path.expanduser(skills_dir))
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def scan(self) -> list[Skill]:
        """Scan directory and return discovered skills."""
        skills: list[Skill] = []
        if not self.skills_dir.exists():
            return skills

        for path in sorted(self.skills_dir.iterdir()):
            try:
                if path.suffix == ".py" and not path.name.startswith("_"):
                    sk = self.load_python_skill(path)
                    if sk is not None:
                        skills.append(sk)
                elif path.suffix == ".md":
                    sk = self.load_markdown_skill(path)
                    if sk is not None:
                        skills.append(sk)
            except Exception as exc:
                logger.warning("Failed to load skill %s: %s", path.name, exc)

        return skills

    def load_python_skill(self, path: Path) -> Optional[Skill]:
        """Import a ``.py`` file and extract functions decorated with ``@skill``."""
        spec = importlib.util.spec_from_file_location(path.stem, str(path))
        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("Error executing skill module %s: %s", path.name, exc)
            return None

        # Find @skill-decorated functions
        skill_name = path.stem
        skill_desc = ""

        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if callable(obj) and getattr(obj, "_nova_skill", False):
                skill_name = getattr(obj, "_skill_name", path.stem)
                skill_desc = getattr(obj, "_skill_description", "")
                break

        return Skill(
            id=path.stem,
            name=skill_name,
            description=skill_desc,
            source_path=str(path),
            skill_type="python",
            loaded_at=datetime.now(),
        )

    def load_markdown_skill(self, path: Path) -> Optional[Skill]:
        """Parse a ``.md`` file — extract name from H1, description from first paragraph."""
        text = path.read_text(encoding="utf-8")
        lines = text.strip().splitlines()

        name = path.stem
        description = ""

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("# ") and name == path.stem:
                name = stripped.lstrip("# ").strip()
            elif stripped and not stripped.startswith("#") and not description:
                description = stripped
                break

        return Skill(
            id=path.stem,
            name=name,
            description=description,
            source_path=str(path),
            skill_type="markdown",
            loaded_at=datetime.now(),
        )
