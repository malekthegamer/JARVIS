"""Slice-2 stage-4 tests: the single-step agent loop. A tool-calling fake
provider drives brain -> executor -> tool result -> final prose, with the
EXECUTING state visible in between; hostile paths (unknown tool, crashing
primitive) return strings, never exceptions; live Gemini keeps pure
questions out of EXECUTING."""
from __future__ import annotations

import pytest

from jarvis import primitives
from jarvis.providers.brain.base import BrainProvider, BrainResponse, ToolCall
from jarvis.state import AgentState, broadcaster


class ToolCallingProvider(BrainProvider):
    """First round: emit one tool call. Second round: read the tool result
    and produce the final prose."""
    supports_tools = True

    def __init__(self, tool_name="launch_app", tool_args=None):
        self.tool_name = tool_name
        self.tool_args = tool_args or {"name": "notepad"}
        self.rounds = 0
        self.seen_tool_result = None

    def is_configured(self):
        return True

    def generate(self, messages, system_prompt, tools=None):
        self.rounds += 1
        if self.rounds == 1:
            return BrainResponse(tool_calls=[
                ToolCall(id="t1", name=self.tool_name, args=self.tool_args)])
        self.seen_tool_result = next(
            (m["content"] for m in reversed(messages) if m["role"] == "tool"), None)
        return BrainResponse(text=f"Done, sir. ({self.seen_tool_result})")


@pytest.fixture()
def state_log():
    events: list[dict] = []
    unsubscribe = broadcaster.subscribe(events.append)
    yield events
    unsubscribe()


def _make_brain(provider):
    from jarvis.brain import JarvisBrain
    brain = JarvisBrain()
    brain._provider_override = provider
    return brain


def test_tool_round_walks_executing_state(monkeypatch, state_log):
    calls = []
    monkeypatch.setitem(primitives.PRIMITIVES["launch_app"], "fn",
                        lambda args: calls.append(args) or "LAUNCHED. VERIFY: ok.")
    provider = ToolCallingProvider()
    brain = _make_brain(provider)
    reply = brain.think("open notepad")

    assert calls == [{"name": "notepad"}]
    assert "VERIFY: ok" in reply
    assert provider.seen_tool_result == "LAUNCHED. VERIFY: ok."

    states = [e["state"] for e in state_log]
    assert states == ["thinking", "executing", "thinking", "idle"]
    detail = next(e["detail"] for e in state_log if e["state"] == "executing")
    assert detail == "launch_app"
    seqs = [e["seq"] for e in state_log]
    assert seqs == sorted(seqs)
    assert broadcaster.current is AgentState.IDLE


def test_unknown_tool_returns_string_not_crash(state_log):
    provider = ToolCallingProvider(tool_name="bogus_tool", tool_args={})
    brain = _make_brain(provider)
    reply = brain.think("do the thing")
    assert "Unknown tool" in provider.seen_tool_result
    assert reply  # loop completed to prose
    assert broadcaster.current is AgentState.IDLE


def test_crashing_primitive_is_contained(monkeypatch, state_log):
    def boom(args):
        raise RuntimeError("primitive exploded")
    monkeypatch.setitem(primitives.PRIMITIVES["launch_app"], "fn", boom)
    provider = ToolCallingProvider()
    brain = _make_brain(provider)
    reply = brain.think("open notepad")
    assert "primitive exploded" in provider.seen_tool_result
    assert reply
    # EXECUTING was entered and cleanly left despite the crash
    states = [e["state"] for e in state_log]
    assert "executing" in states
    assert states[-1] == "idle"


def test_tools_schema_exposed_to_brain():
    from jarvis.brain import JarvisBrain
    names = [t["name"] for t in JarvisBrain().tools()]
    assert "launch_app" in names and "read_ui_tree" in names


def test_live_question_never_enters_executing(state_log):
    """Intent split: a pure question must stay conversational."""
    from jarvis import config
    if not config.get_api_key("gemini"):
        pytest.skip("GEMINI_API_KEY not configured")
    from jarvis.brain import JarvisBrain
    brain = JarvisBrain()
    reply = brain.think("What is 2+2? Reply with just the number.")
    assert "4" in reply, reply
    assert all(e["state"] != "executing" for e in state_log), state_log