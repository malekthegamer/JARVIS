"""JarvisBrain — the one orchestrator every interface (terminal, HUD) wraps.

Salvaged from legacy/brain.py and trimmed for the walking skeleton: the
provider-agnostic tool-calling loop stays (slice 3 plugs skills into it),
but no tools are registered yet and the memory/skill systems are gone.
State is emitted through jarvis.state.broadcaster — THINKING on entry,
IDLE restored in `finally` so an error can never strand the HUD.
"""
from __future__ import annotations

import threading

from jarvis.core import chain
from jarvis.core.errors import ProviderError
from jarvis.core.settings_store import settings
from jarvis.providers import registry
from jarvis.providers.brain.base import BrainResponse
from jarvis.state import AgentState, broadcaster

BASE_SYSTEM_PROMPT = """You are JARVIS, a witty and highly capable personal AI assistant inspired by
Iron Man. You run on the user's Windows PC. You can open applications
(launch_app), inspect the screen (read_ui_tree), close windows
(close_window), delete files from your workspace (delete_file), and operate
inside apps: click elements (click), type text (type_text), and press keys
(press_keys). When you click or type, pass the 'window' you are working in
(e.g. 'Notepad') so the action targets the right place. type_text does NOT
press Enter — to submit or save, use press_keys separately (that will be
confirmation-gated). Every action tool returns a VERIFY report — relay its
verdict honestly, including failures and 'not confirmed' results.
For any task that needs MORE THAN ONE action, first call plan_steps with a
short ordered list of the steps you intend to take — the user watches that
plan progress on their HUD. Execute one step at a time and check each
tool's VERIFY verdict before moving on. If a step fails or the screen
isn't what you expected, do not blindly repeat the same action: look again
(read_ui_tree) and call plan_steps again with a revised plan. Note that
read_ui_tree is shallow — a control can exist without appearing in it. To
act on something specific you believe is on screen, just try click with a
precise name (e.g. 'Play Discover Weekly'): its resolver searches far
deeper and lists near-miss candidates when it fails — use those candidate
names on your next attempt. Before declaring a task done, VERIFY the
outcome with a final observation (read_ui_tree) — never claim a result
(e.g. 'it is playing') that a tool did not confirm. When you finish — or
stop early — tell the user honestly which steps completed and which did
not.
Destructive or committal actions are confirmation-gated: the user sees a
prompt and may decline or ignore it. A CANCELLED tool result is final —
acknowledge it gracefully and NEVER retry a cancelled action. Abilities not
yet wired up (wider file access, running shell commands, system settings,
the web) — if asked, say so rather than pretending to act. Keep
responses concise and conversational unless the user asks for detail.
Address the user as 'sir' occasionally, but don't overdo it. You never
initiate conversation or speech on your own — you only respond. Your
responses are spoken aloud, so avoid markdown, code fences, and bullet
lists unless the user is clearly working in text."""

# 12, raised from 8 after the slice-6 live acceptance: spec script #1
# ("open Spotify, play Discover Weekly") measures ~10 rounds when clean
# (plan + launch + observe + search-click + type + enter + observe +
# playlist-click + play + verify) — 8 exhausted every run. +2 slack covers
# one failure/re-observe recovery. The breaker + failure budget remain the
# real runaway guards; this is only the outer bound.
MAX_TOOL_ROUNDS = 12
MAX_PLAN_STEPS = 20  # a longer "plan" is noise, not a plan

# Brain-level meta-tool (slice 6): declares/revises the visible plan. It
# touches no OS surface, so it lives here — NOT in the primitives registry
# (no tier gate, no act/observe/verify wrapper).
PLAN_STEPS_SCHEMA = {
    "name": "plan_steps",
    "description": ("Declare (or revise) your ordered plan BEFORE a multi-step "
                    "task: a short list of the steps you intend to take, in "
                    "order. The user sees this plan on their HUD and watches it "
                    "progress. Call it again with a new list if you change "
                    "approach after a failure."),
    "parameters": {
        "type": "object",
        "properties": {"steps": {
            "type": "array", "items": {"type": "string"},
            "description": "Ordered, short, human-readable step descriptions"}},
        "required": ["steps"],
    },
}


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
        return primitives.tools_schema() + [PLAN_STEPS_SCHEMA]

    # ---------- the one call every interface uses ----------
    def think(self, user_message: str) -> str:
        with self._lock:
            broadcaster.set(AgentState.THINKING)
            tracker = chain.start()  # one chain per interaction; ground truth for the HUD
            status = "error"  # anything that escapes _think_inner ends the chain honestly
            try:
                reply = self._think_inner(user_message)
                status = "done"
                return reply
            except ProviderError as exc:
                return exc.friendly()
            except Exception as exc:  # absolute last resort — never crash a run loop
                return f"Something went wrong on my end: {exc}"
            finally:
                # an aborted chain (cancelled/budget/exhausted) names its reason
                chain.clear(tracker.aborted or status)
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
                tracker = chain.current()
                blocked = tracker.pre_call_guard(tc.name, tc.args or {}) \
                    if tracker else None
                if blocked is not None:
                    result = blocked  # synthetic result — the call never runs
                elif tracker:
                    n = tracker.begin_call(tc.name, tc.args or {})
                    result = self._execute_tool(tc.name, tc.args)
                    tracker.end_call(n, chain.status_from_result(str(result)),
                                     note=str(result))
                else:
                    result = self._execute_tool(tc.name, tc.args)
                self.history.append({
                    "role": "tool", "tool_call_id": tc.id, "name": tc.name,
                    "content": str(result)[:4000],
                })
        return self._exhausted_reply()

    def _exhausted_reply(self) -> str:
        """MAX_TOOL_ROUNDS ran out mid-chain: report progress honestly."""
        tracker = chain.current()
        if tracker and tracker.calls:
            tracker.aborted = tracker.aborted or "exhausted"
            return ("I've hit my action limit for one request and stopped "
                    f"myself, sir. {tracker.progress_summary()}")
        return "I got stuck in a tool loop, sir — I've stopped myself. Try rephrasing."

    # ---------- tool routing ----------
    def _execute_tool(self, name: str, args: dict) -> str:
        if name == "plan_steps":
            return self._plan_steps(args or {})
        from jarvis import primitives  # lazy
        return primitives.execute(name, args or {})

    def _plan_steps(self, args: dict) -> str:
        """Meta-tool: record + broadcast the declared plan. Never raises."""
        raw = args.get("steps") or []
        steps = [str(s).strip() for s in raw if str(s).strip()]
        if not steps:
            return "FAILED: plan_steps needs a non-empty list of step descriptions."
        if len(steps) > MAX_PLAN_STEPS:
            return (f"FAILED: that plan is too long ({len(steps)} steps) — "
                    f"declare at most {MAX_PLAN_STEPS}, broader strokes.")
        tracker = chain.current()
        if tracker:
            tracker.set_plan(steps)
        numbered = "; ".join(f"{i}) {s}" for i, s in enumerate(steps, 1))
        return f"PLAN SET ({len(steps)} steps): {numbered}. Now execute step 1."

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
