"""Claude brain via the official anthropic SDK."""
from __future__ import annotations

import config
from core.errors import ProviderError, classify_exception
from core.settings_store import settings
from providers.brain.base import BrainProvider, BrainResponse, ToolCall
from providers.registry import register


@register("brain", "claude")
class ClaudeProvider(BrainProvider):
    requires_api_key = True
    supports_tools = True

    def is_configured(self) -> bool:
        return config.get_api_key("claude") is not None

    def generate(self, messages, system_prompt, tools=None) -> BrainResponse:
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError("generic", "Claude", f"anthropic not installed: {exc}")
        key = config.get_api_key("claude")
        if not key:
            raise ProviderError("missing_key", "Claude")

        api_messages: list[dict] = []
        for msg in messages:
            if msg["role"] == "user":
                api_messages.append({"role": "user", "content": msg["content"]})
            elif msg["role"] == "assistant":
                blocks: list[dict] = []
                if msg.get("content"):
                    blocks.append({"type": "text", "text": msg["content"]})
                for tc in msg.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["args"]})
                if blocks:
                    api_messages.append({"role": "assistant", "content": blocks})
            elif msg["role"] == "tool":
                api_messages.append({"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": msg["tool_call_id"], "content": msg["content"],
                }]})

        kwargs: dict = {
            "model": settings.get("brain.models.claude", "claude-sonnet-5"),
            "max_tokens": 2048,
            "system": system_prompt,
            "messages": api_messages,
        }
        if tools:
            kwargs["tools"] = [{"name": t["name"], "description": t["description"],
                                "input_schema": t["parameters"]} for t in tools]
        try:
            resp = anthropic.Anthropic(api_key=key).messages.create(**kwargs)
        except Exception as exc:
            raise classify_exception(exc, "Claude")

        out = BrainResponse()
        for block in resp.content:
            if block.type == "text":
                out.text += block.text
            elif block.type == "tool_use":
                out.tool_calls.append(ToolCall(id=block.id, name=block.name, args=dict(block.input or {})))
        return out
