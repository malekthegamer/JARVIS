"""BrainProvider interface + the neutral message/tool format shared by all brains.

Neutral history format (provider-agnostic; each provider translates to its SDK):
    {"role": "user",      "content": str}
    {"role": "assistant", "content": str, "tool_calls": [ToolCall-dict, ...]}
    {"role": "tool",      "tool_call_id": str, "name": str, "content": str}

Neutral tool schema (OpenAI-style function definition):
    {"name": str, "description": str, "parameters": {JSON schema}}
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "args": self.args}


@dataclass
class BrainResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class BrainProvider:
    name: str = "base"
    requires_api_key: bool = True
    supports_tools: bool = False

    def generate(self, messages: list[dict], system_prompt: str,
                 tools: list[dict] | None = None) -> BrainResponse:
        """Run one model turn. Raise ProviderError (never a raw SDK error) on failure."""
        raise NotImplementedError

    def is_configured(self) -> bool:
        raise NotImplementedError
