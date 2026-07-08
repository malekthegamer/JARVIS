"""JarvisBrain — the one orchestrator every interface (terminal, HUD) wraps.

Salvaged from legacy/brain.py and trimmed for the walking skeleton: the
provider-agnostic tool-calling loop stays (slice 3 plugs skills into it),
but no tools are registered yet and the memory/skill systems are gone.
State is emitted through jarvis.state.broadcaster — THINKING on entry,
IDLE restored in `finally` so an error can never strand the HUD.
"""
from __future__ import annotations

import threading

from jarvis.core.errors import ProviderError
from jarvis.core.settings_store import settings
from jarvis.providers import registry
from jarvis.providers.brain.base import BrainResponse
from jarvis.state import AgentState, broadcaster

BASE_SYSTEM_PROMPT = """You are JARVIS, a witty and highly capable personal AI assistant inspired by
Iron Man. You run on the user's Windows PC. You can open applications
(launch_app) and inspect what is on screen (read_ui_tree); every action tool
returns a VERIFY report — relay its verdict honestly, including failures.
Other PC-control abilities (clicking, typing, files, web) are still being
wired up — if asked for one, say so rather than pretending to act. Keep
responses concise and conversational unless the user asks for detail.
Address the user as 'sir' occasionally, but don't overdo it. You never
initiate conversation or speech on your own — you only respond. Your
responses are spoken aloud, so avoid markdown, code fences, and bullet
lists unless the user is clearly working in text."""

MAX_TOOL_ROUNDS = 8


class JarvisBrain:
    def __init__(self) -> None:
        self.history: list[dict] = []
        self._lock = threading.RLock()
        self._provider_override = None  # tests inject a fake here

    # ---------- provider / prompt ----------
    def provider(self):
        if self._provider_override is not None:
            return self._provider_override
        name = settings.get("brain.active", "gemini")
        provider = registry.get("brain", name)
        if provider is None:
            raise ProviderError("generic", name, "unknown brain provider")
        return provider

    def system_prompt(self) -> str:
        return BASE_SYSTEM_PROMPT

    def tools(self) -> list[dict]:
        from jarvis import primitives  # lazy — text-only paths skip the import
        return primitives.tools_schema()

    # ---------- the one call every interface uses ----------
    def think(self, user_message: str) -> str:
        with self._lock:
            broadcaster.set(AgentState.THINKING)
            try:
                return self._think_inner(user_message)
            except ProviderError as exc:
                return exc.friendly()
            except Exception as exc:  # absolute last resort — never crash a run loop
                return f"Something went wrong on my end: {exc}"
            finally:
                broadcaster.set(AgentState.IDLE)

    def _think_inner(self, user_message: str) -> str:
        provider = self.provider()
        self.history.append({"role": "user", "content": user_message})
        self._trim()

        tools = (self.tools() or None) if provider.supports_tools else None
        for _round in range(MAX_TOOL_ROUNDS):
            try:
                resp: BrainResponse = provider.generate(self.history, self.system_prompt(), tools=tools)
            except ProviderError as exc:
                if tools and exc.kind in ("bad_response", "generic"):
                    # Some models/providers choke on tool schemas — retry plain.
                    tools = None
                    continue
                if self.history and self.history[-1]["role"] == "user":
                    self.history.pop()  # keep history coherent for the next attempt
                raise

            if not resp.tool_calls:
                text = resp.text.strip() or "…"
                self.history.append({"role": "assistant", "content": text})
                return text

            # Tool round: record the assistant turn, execute each call, loop.
            self.history.append({
                "role": "assistant",
                "content": resp.text,
                "tool_calls": [tc.as_dict() for tc in resp.tool_calls],
            })
            for tc in resp.tool_calls:
                result = self._execute_tool(tc.name, tc.args)
                self.history.append({
                    "role": "tool", "tool_call_id": tc.id, "name": tc.name,
                    "content": str(result)[:4000],
                })
        return "I got stuck in a tool loop, sir — I've stopped myself. Try rephrasing."

    # ---------- tool routing ----------
    def _execute_tool(self, name: str, args: dict) -> str:
        from jarvis import primitives  # lazy
        return primitives.execute(name, args or {})

    # ---------- housekeeping ----------
    def _trim(self) -> None:
        limit = int(settings.get("history_max_messages", 40))
        if len(self.history) <= limit:
            return
        trimmed = self.history[-limit:]
        # Never start history on a tool/assistant continuation — drop to the
        # first user message so every provider sees a coherent transcript.
        while trimmed and trimmed[0]["role"] != "user":
            trimmed.pop(0)
        self.history = trimmed

    def reset(self) -> None:
        with self._lock:
            self.history = []


jarvis_brain = JarvisBrain()
