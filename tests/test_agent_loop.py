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


# ---------- slice 3: CONFIRM tier gate ----------

import threading
import time as _time

from jarvis.core.confirmations import confirmations
from jarvis.primitives import files


@pytest.fixture()
def confirm_events():
    log: list[dict] = []
    unsubscribe = confirmations.subscribe(log.append)
    yield log
    unsubscribe()


@pytest.fixture()
def workspace_file():
    files.AGENT_FILES_DIR.mkdir(parents=True, exist_ok=True)
    f = files.AGENT_FILES_DIR / "gate-test.txt"
    f.write_text("gate", encoding="utf-8")
    yield f
    f.unlink(missing_ok=True)


def _auto_resolver(approved: bool):
    """Answer the next confirm_request like a user clicking a modal button."""
    def responder(event):
        if event.get("type") == "confirm_request":
            threading.Thread(
                target=lambda: (_time.sleep(0.05),
                                confirmations.resolve(event["id"], approved)),
            ).start()
    return confirmations.subscribe(responder)


def test_confirm_approved_full_walk(state_log, confirm_events, workspace_file):
    unsubscribe = _auto_resolver(approved=True)
    try:
        provider = ToolCallingProvider(tool_name="delete_file",
                                       tool_args={"name": "gate-test.txt"})
        brain = _make_brain(provider)
        reply = brain.think("delete gate-test.txt from your workspace")
    finally:
        unsubscribe()

    assert not workspace_file.exists(), "approved deletion must actually happen"
    assert "Deleted" in provider.seen_tool_result
    states = [e["state"] for e in state_log]
    assert states == ["thinking", "confirming", "executing", "thinking", "idle"]
    assert "gate-test.txt" in confirm_events[0]["description"]
    assert reply


def test_confirm_denied_nothing_runs(state_log, confirm_events, workspace_file):
    unsubscribe = _auto_resolver(approved=False)
    try:
        provider = ToolCallingProvider(tool_name="delete_file",
                                       tool_args={"name": "gate-test.txt"})
        brain = _make_brain(provider)
        brain.think("delete gate-test.txt from your workspace")
    finally:
        unsubscribe()

    assert workspace_file.exists(), "declined action must not run"
    assert "CANCELLED" in provider.seen_tool_result
    assert "do not retry" in provider.seen_tool_result.lower()
    states = [e["state"] for e in state_log]
    assert "confirming" in states
    assert "executing" not in states, "denied gate must never reach EXECUTING"
    assert states[-1] == "idle"
    kinds = [e["type"] for e in confirm_events]
    assert kinds.count("confirm_request") == 1, "no re-prompting after a decline"


def test_confirm_timeout_fails_safe(state_log, workspace_file):
    from jarvis.core.settings_store import settings
    settings.set("confirm.timeout_s", 0.3, persist=False)
    try:
        provider = ToolCallingProvider(tool_name="delete_file",
                                       tool_args={"name": "gate-test.txt"})
        brain = _make_brain(provider)
        brain.think("delete gate-test.txt")
    finally:
        settings.set("confirm.timeout_s", 30, persist=False)

    assert workspace_file.exists(), "timeout must cancel, never proceed"
    assert "CANCELLED" in provider.seen_tool_result
    assert "no response" in provider.seen_tool_result
    assert broadcaster.current is AgentState.IDLE


def test_no_modal_for_nonexistent_window(state_log, confirm_events):
    provider = ToolCallingProvider(tool_name="close_window",
                                   tool_args={"title": "xyzzy-window-that-does-not-exist"})
    brain = _make_brain(provider)
    brain.think("close that window")

    assert confirm_events == [], "no pointless modal for a missing target"
    assert "FAILED" in provider.seen_tool_result
    states = [e["state"] for e in state_log]
    assert "confirming" not in states


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