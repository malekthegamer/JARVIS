"""Central skill registry — skills self-register; the brain discovers them here.

Adding a skill = drop one file in skills/ with a @register_skill class.
No core file lists skills by hand (skills/__init__.py just imports the modules).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from skills.base import Skill

_skills: dict[str, "Skill"] = {}


def register_skill(cls):
    """Class decorator: instantiate and register a Skill."""
    instance = cls()
    _skills[instance.name] = instance
    return cls


def all_skills() -> dict[str, "Skill"]:
    return dict(_skills)


def get_skill(name: str) -> "Skill | None":
    return _skills.get(name)


def tools_schema() -> list[dict]:
    """Aggregate every skill's tool definitions (neutral OpenAI-style schema)
    for registration with tool-calling brain providers."""
    tools: list[dict] = []
    for skill in _skills.values():
        try:
            tools.extend(skill.tools())
        except Exception:
            continue
    return tools


def execute_tool(name: str, args: dict) -> str:
    """Route a brain tool call to the skill that owns it."""
    for skill in _skills.values():
        try:
            if name in skill.tool_names():
                return skill.execute(name, args or {})
        except Exception as exc:
            return f"Error in {name}: {exc}"
    return f"Unknown tool: {name}"


def capabilities_text() -> str:
    """Human-readable capability list appended to the system prompt."""
    if not _skills:
        return ""
    lines = "\n".join(f"- {s.name}: {s.description}" for s in _skills.values())
    return f"\nYour currently available skills:\n{lines}\n"


def load_all() -> None:
    """Import skills package so every module's @register_skill runs."""
    try:
        import skills  # noqa: F401  (side-effect import)
    except ImportError:
        pass  # Phase 1: no skills yet — registry stays empty by design
