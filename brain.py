"""JarvisBrain — the one orchestrator every interface (terminal, web) wraps.

Loads whichever BrainProvider is active in settings, manages/trims the session
history, injects long-term memories into the system prompt, exposes
think(user_message) regardless of the provider behind it, and routes tool
calls to skills (plus the built-in memory tools).
"""
from __future__ import annotations

import re
import threading
from typing import Callable

from core import audit_log, memory, skill_registry
from core.errors import ProviderError
from core.settings_store import settings
from providers import registry
from providers.brain.base import BrainResponse

BASE_SYSTEM_PROMPT = """You are JARVIS, a witty and highly capable personal AI assistant inspired by
Iron Man. You run on the user's Windows PC. You can hold conversations, control
the PC, manage files, research topics, monitor the system, help with code, and
handle productivity tasks. Keep responses concise and conversational unless the
user asks for detail. Address the user as 'sir' occasionally, but don't overdo
it. When you take an action on the user's behalf (opening an app, deleting a
file, sending a message), confirm what you did in one short sentence.
You never initiate conversation or speech on your own — you only respond.
When the user asks you to remember something, use the remember_fact tool.
Your responses are often spoken aloud, so avoid markdown, code fences, and
bullet lists unless the user is clearly working in text."""

MAX_TOOL_ROUNDS = 8

_MEMORY_TOOLS = [
    {
        "name": "remember_fact",
        "description": "Store a durable fact about the user (name, preference, routine) in long-term memory. Survives restarts.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Short label for the fact, e.g. 'my name', 'favorite editor'"},
                "value": {"type": "string", "description": "The fact itself"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "forget_fact",
        "description": "Delete a fact from long-term memory by its key.",
        "parameters": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Label of the fact to forget"}},
            "required": ["key"],
        },
    },
    {
        "name": "list_facts",
        "description": "List everything currently stored in long-term memory.",
        "parameters": {"type": "object", "properties": {}},
    },
]


class JarvisBrain:
    def __init__(self) -> None:
        self.history: list[dict] = []
        self._lock = threading.RLock()
        self.on_status: Callable[[str], None] | None = None  # dashboard hook

    # ---------- provider / prompt ----------
    def provider(self):
        name = settings.get("brain.active", "gemini")
        provider = registry.get("brain", name)
        if provider is None:
            raise ProviderError("generic", name, "unknown brain provider")
        return provider

    def system_prompt(self) -> str:
        return BASE_SYSTEM_PROMPT + memory.context_block() + skill_registry.capabilities_text()

    def tools(self) -> list[dict]:
        return _MEMORY_TOOLS + skill_registry.tools_schema()

    # ---------- the one call every interface uses ----------
    def think(self, user_message: str) -> str:
        with self._lock:
            self._status("thinking")
            try:
                return self._think_inner(user_message)
            except ProviderError as exc:
                return exc.friendly()
            except Exception as exc:  # absolute last resort — never crash a run loop
                return f"Something went wrong on my end: {exc}"
            finally:
                self._status("idle")

    def _think_inner(self, user_message: str) -> str:
        provider = self.provider()
        self.history.append({"role": "user", "content": user_message})
        self._trim()

        tools = self.tools() if provider.supports_tools else None
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
                self._status(f"executing:{tc.name}")
                result = self._execute_tool(tc.name, tc.args)
                self.history.append({
                    "role": "tool", "tool_call_id": tc.id, "name": tc.name,
                    "content": str(result)[:4000],
                })
        return "I got stuck in a tool loop, sir — I've stopped myself. Try rephrasing."

    # ---------- tool routing ----------
    def _execute_tool(self, name: str, args: dict) -> str:
        args = args or {}
        if name == "remember_fact":
            audit_log.log_action("memory", "remember", {"key": args.get("key", "")})
            return memory.remember(args.get("key", "note"), str(args.get("value", "")))
        if name == "forget_fact":
            audit_log.log_action("memory", "forget", {"key": args.get("key", "")})
            return "Forgotten." if memory.forget(args.get("key", "")) else "I had nothing stored under that."
        if name == "list_facts":
            facts = memory.list_memories()
            return "\n".join(f"{k}: {v}" for k, v in facts.items()) or "Long-term memory is empty."
        return skill_registry.execute_tool(name, args)

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

    def _status(self, state: str) -> None:
        if self.on_status:
            try:
                self.on_status(state)
            except Exception:
                pass


def handle_memory_fallback(text: str) -> str | None:
    """Regex fallback for brains without working tool support:
    'remember (that) X is Y' → store directly. Returns a reply or None."""
    m = re.match(r"(?i)^\s*(?:please\s+)?remember\s+(?:that\s+)?(.+)$", text.strip())
    if not m:
        return None
    fact = m.group(1).rstrip(".")
    if " is " in fact:
        key, value = fact.split(" is ", 1)
    else:
        key, value = f"note {len(memory.list_memories()) + 1}", fact
    audit_log.log_action("memory", "remember", {"key": key})
    memory.remember(key, value)
    return f"Noted, sir — {key.strip()}: {value.strip()}."


jarvis_brain = JarvisBrain()
