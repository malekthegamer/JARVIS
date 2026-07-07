"""Ollama brain — local models on the user's GPU, no API key, fully offline.

Talks to the local Ollama server's /api/chat. Tool use is passed through for
models that support it; models that don't get an automatic retry without tools.
"""
from __future__ import annotations

import json

import requests

import config
from core.errors import ProviderError, classify_exception
from core.settings_store import settings
from providers.brain.base import BrainProvider, BrainResponse, ToolCall
from providers.registry import register


@register("brain", "ollama")
class OllamaProvider(BrainProvider):
    requires_api_key = False
    supports_tools = True  # depends on model; auto-degrades below

    def is_configured(self) -> bool:
        try:
            return requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=1).ok
        except requests.RequestException:
            return False

    @staticmethod
    def list_models() -> list[str]:
        """Installed local models, for the settings dropdown."""
        try:
            resp = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=3)
            resp.raise_for_status()
            return [m["name"] for m in resp.json().get("models", [])]
        except (requests.RequestException, ValueError, KeyError):
            return []

    def _translate(self, messages: list[dict], system_prompt: str) -> list[dict]:
        out = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            if msg["role"] == "assistant" and msg.get("tool_calls"):
                out.append({"role": "assistant", "content": msg.get("content") or "",
                            "tool_calls": [{"function": {"name": tc["name"], "arguments": tc["args"]}}
                                           for tc in msg["tool_calls"]]})
            elif msg["role"] == "tool":
                out.append({"role": "tool", "content": msg["content"]})
            else:
                out.append({"role": msg["role"], "content": msg["content"]})
        return out

    def generate(self, messages, system_prompt, tools=None) -> BrainResponse:
        model = settings.get("brain.models.ollama", "llama3.1:8b")
        payload: dict = {
            "model": model,
            "messages": self._translate(messages, system_prompt),
            "stream": False,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]

        try:
            resp = requests.post(f"{config.OLLAMA_URL}/api/chat", json=payload, timeout=300)
            if resp.status_code == 400 and tools:
                # Model likely lacks tool support — degrade gracefully, retry plain.
                payload.pop("tools")
                resp = requests.post(f"{config.OLLAMA_URL}/api/chat", json=payload, timeout=300)
            if resp.status_code == 404:
                raise ProviderError("bad_response", "Ollama",
                                    f"model '{model}' not found — run: ollama pull {model}")
            resp.raise_for_status()
            data = resp.json()
        except ProviderError:
            raise
        except requests.exceptions.ConnectionError:
            raise ProviderError("connection", "Ollama", "is the Ollama server running? (ollama serve)")
        except Exception as exc:
            raise classify_exception(exc, "Ollama")

        message = data.get("message", {})
        out = BrainResponse(text=message.get("content", ""))
        for i, tc in enumerate(message.get("tool_calls") or []):
            fn = tc.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            out.tool_calls.append(ToolCall(id=f"ollama-{i}", name=fn.get("name", ""), args=args))
        return out
