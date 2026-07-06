"""OpenAI brain (chat.completions). Also the base class for OpenRouter, which
speaks the same protocol at a different base URL."""
from __future__ import annotations

import json

import config
from core.errors import ProviderError, classify_exception
from core.settings_store import settings
from providers.brain.base import BrainProvider, BrainResponse, ToolCall
from providers.registry import register


class OpenAICompatibleProvider(BrainProvider):
    """Shared OpenAI-protocol implementation."""
    requires_api_key = True
    supports_tools = True
    provider_label = "OpenAI"
    key_name = "openai"
    base_url: str | None = None
    model_setting = "brain.models.openai"
    default_model = "gpt-4o-mini"

    def is_configured(self) -> bool:
        return config.get_api_key(self.key_name) is not None

    def _client(self):
        from openai import OpenAI
        key = config.get_api_key(self.key_name)
        if not key:
            raise ProviderError("missing_key", self.provider_label)
        kwargs = {"api_key": key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return OpenAI(**kwargs)

    def _translate(self, messages: list[dict], system_prompt: str) -> list[dict]:
        out = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            if msg["role"] == "assistant" and msg.get("tool_calls"):
                out.append({
                    "role": "assistant",
                    "content": msg.get("content") or None,
                    "tool_calls": [{
                        "id": tc["id"], "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])},
                    } for tc in msg["tool_calls"]],
                })
            elif msg["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": msg["tool_call_id"], "content": msg["content"]})
            else:
                out.append({"role": msg["role"], "content": msg["content"]})
        return out

    def generate(self, messages, system_prompt, tools=None) -> BrainResponse:
        kwargs: dict = {
            "model": settings.get(self.model_setting, self.default_model),
            "messages": self._translate(messages, system_prompt),
        }
        if tools:
            kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
        try:
            resp = self._client().chat.completions.create(**kwargs)
            choice = resp.choices[0].message
        except ProviderError:
            raise
        except Exception as exc:
            raise classify_exception(exc, self.provider_label)

        out = BrainResponse(text=choice.content or "")
        for tc in choice.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            out.tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, args=args))
        return out


@register("brain", "openai")
class OpenAIProvider(OpenAICompatibleProvider):
    pass
