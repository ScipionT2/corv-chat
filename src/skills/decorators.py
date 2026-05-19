"""Decorators for defining Nova skills."""

from __future__ import annotations

from typing import Callable, Optional


def skill(name: Optional[str] = None, description: str = "") -> Callable:
    """Decorator to mark a function as a Nova skill.

    Usage::

        @skill(name="Calculator", description="Do math")
        def calculate(expression: str) -> str:
            ...
    """

    def wrapper(func: Callable) -> Callable:
        func._nova_skill = True  # type: ignore[attr-defined]
        func._skill_name = name or func.__name__  # type: ignore[attr-defined]
        func._skill_description = description or func.__doc__ or ""  # type: ignore[attr-defined]
        return func

    return wrapper
