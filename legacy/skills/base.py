"""Skill plugin interface.

A skill bundles one or more brain-callable tools. The brain discovers skills
through core.skill_registry (populated by @register_skill on import), gets each
skill's tool schemas via tools(), and routes a tool call to execute().

Destructive tools MUST gate on core.confirmations.confirm_action() before acting.
"""
from __future__ import annotations

from core import audit_log


class Skill:
    name: str = "base"
    description: str = ""

    def tools(self) -> list[dict]:
        """OpenAI-style function schemas this skill exposes to the brain."""
        return []

    def tool_names(self) -> set[str]:
        return {t["name"] for t in self.tools()}

    def execute(self, tool: str, args: dict) -> str:
        """Run one tool by name. Return a short human-readable result string."""
        raise NotImplementedError

    # -- helpers for subclasses --
    def log(self, action: str, params: dict | None = None, result: str = "ok") -> None:
        audit_log.log_action(self.name, action, params or {}, result)


def tool(name: str, description: str, properties: dict | None = None,
         required: list[str] | None = None) -> dict:
    """Build a function schema without the JSON-schema boilerplate."""
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
        },
    }


def prop(kind: str, description: str, **extra) -> dict:
    d = {"type": kind, "description": description}
    d.update(extra)
    return d
