"""Gemini brain via the google-genai SDK (NOT the deprecated google-generativeai)."""
from __future__ import annotations

import config
from core.errors import ProviderError, classify_exception
from core.settings_store import settings
from providers.brain.base import BrainProvider, BrainResponse, ToolCall
from providers.registry import register


@register("brain", "gemini")
class GeminiProvider(BrainProvider):
    requires_api_key = True
    supports_tools = True

    def __init__(self) -> None:
        self._cached_client = None
        self._cached_key: str | None = None

    def is_configured(self) -> bool:
        return config.get_api_key("gemini") is not None

    def _client(self):
        from google import genai
        key = config.get_api_key("gemini")
        if not key:
            raise ProviderError("missing_key", "Gemini")
        # Reuse one client across calls (a fresh per-call client gets its httpx
        # connection torn down -> "client has been closed"). Rebuild only when
        # the key changes, so a Settings key update still takes effect.
        if self._cached_client is None or self._cached_key != key:
            self._cached_client = genai.Client(api_key=key)
            self._cached_key = key
        return self._cached_client

    def generate(self, messages, system_prompt, tools=None) -> BrainResponse:
        try:
            from google.genai import types
        except ImportError as exc:
            raise ProviderError("generic", "Gemini", f"google-genai not installed: {exc}")

        contents = []
        for msg in messages:
            role = msg["role"]
            if role == "user":
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=msg["content"])]))
            elif role == "assistant":
                parts = []
                if msg.get("content"):
                    parts.append(types.Part.from_text(text=msg["content"]))
                for tc in msg.get("tool_calls", []):
                    fc_part = types.Part.from_function_call(name=tc["name"], args=tc["args"])
                    # Gemini 3.x REQUIRES the thought_signature from the original
                    # function call to be echoed back, or the next turn 400s.
                    sig = (tc.get("extra") or {}).get("thought_signature")
                    if sig:
                        fc_part.thought_signature = sig
                    parts.append(fc_part)
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
            elif role == "tool":
                contents.append(types.Content(role="user", parts=[
                    types.Part.from_function_response(name=msg["name"], response={"result": msg["content"]})
                ]))

        gen_config = types.GenerateContentConfig(system_instruction=system_prompt)
        if tools:
            gen_config.tools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t["name"], description=t["description"],
                                          parameters=t["parameters"]) for t in tools
            ])]

        model = settings.get("brain.models.gemini", "gemini-2.5-flash")
        try:
            resp = self._client().models.generate_content(
                model=model, contents=contents, config=gen_config)
        except ProviderError:
            raise
        except Exception as exc:
            raise classify_exception(exc, "Gemini")

        out = BrainResponse()
        try:
            candidate = resp.candidates[0]
            for i, part in enumerate(candidate.content.parts or []):
                if getattr(part, "function_call", None):
                    fc = part.function_call
                    extra = {}
                    sig = getattr(part, "thought_signature", None)
                    if sig:
                        extra["thought_signature"] = sig
                    out.tool_calls.append(ToolCall(
                        id=f"gemini-{i}", name=fc.name, args=dict(fc.args or {}), extra=extra))
                elif getattr(part, "text", None):
                    out.text += part.text
        except (AttributeError, IndexError, TypeError) as exc:
            raise ProviderError("bad_response", "Gemini", str(exc))
        return out
