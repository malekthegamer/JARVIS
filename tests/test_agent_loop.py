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
                        lambda args, gi=None: calls.append(args) or "LAUNCHED. VERIFY: ok.")
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
    def boom(args, gi=None):
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


# ---------- slice 4: input tools through the dynamic-tier gate ----------

def test_type_text_is_auto_no_confirm(monkeypatch, state_log, confirm_events):
    # classify_type -> auto (non-terminal window); fn stubbed so no real typing
    monkeypatch.setitem(primitives.PRIMITIVES["type_text"], "classify",
                        lambda args: {"tier": "auto", "description": "Type", "expect_name": None})
    monkeypatch.setitem(primitives.PRIMITIVES["type_text"], "fn",
                        lambda args, gi=None: "OK: typed. VERIFY: text confirmed present.")
    provider = ToolCallingProvider(tool_name="type_text",
                                   tool_args={"text": "hello world", "window": "Notepad"})
    brain = _make_brain(provider)
    reply = brain.think("type hello world in notepad")

    assert "confirmed" in provider.seen_tool_result
    states = [e["state"] for e in state_log]
    assert "confirming" not in states, "AUTO type must not gate"
    assert "executing" in states
    assert confirm_events == []


def test_ctrl_s_is_confirm_gated(monkeypatch, state_log, confirm_events):
    ran = []
    monkeypatch.setitem(primitives.PRIMITIVES["press_keys"], "classify",
                        lambda args: {"tier": "confirm",
                                      "description": "Press ctrl+s (save) in 'Notepad'",
                                      "expect_name": None})
    monkeypatch.setitem(primitives.PRIMITIVES["press_keys"], "fn",
                        lambda args, gi=None: ran.append(1) or "OK: Pressed ctrl+s.")
    unsubscribe = _auto_resolver(approved=True)
    try:
        provider = ToolCallingProvider(tool_name="press_keys",
                                       tool_args={"combo": "ctrl+s", "window": "Notepad"})
        brain = _make_brain(provider)
        brain.think("save it")
    finally:
        unsubscribe()

    assert ran == [1], "approved ctrl+s must run"
    states = [e["state"] for e in state_log]
    assert states == ["thinking", "confirming", "executing", "thinking", "idle"]
    assert "save" in confirm_events[0]["description"].lower()


def test_ctrl_s_declined_does_not_run(monkeypatch, state_log):
    ran = []
    monkeypatch.setitem(primitives.PRIMITIVES["press_keys"], "classify",
                        lambda args: {"tier": "confirm",
                                      "description": "Press ctrl+s (save) in 'Notepad'",
                                      "expect_name": None})
    monkeypatch.setitem(primitives.PRIMITIVES["press_keys"], "fn",
                        lambda args, gi=None: ran.append(1) or "OK")
    unsubscribe = _auto_resolver(approved=False)
    try:
        provider = ToolCallingProvider(tool_name="press_keys",
                                       tool_args={"combo": "ctrl+s", "window": "Notepad"})
        brain = _make_brain(provider)
        brain.think("save it")
    finally:
        unsubscribe()

    assert ran == [], "declined ctrl+s must NOT run"
    assert "CANCELLED" in provider.seen_tool_result
    assert "executing" not in [e["state"] for e in state_log]


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

# ---------- slice 5: vision-fallback click through the executor + agent loop ----------

import numpy as _np


def _stub_screen(monkeypatch):
    monkeypatch.setattr(primitives.screen, "capture_screen",
                        lambda: _np.zeros((4, 4, 3), dtype="uint8"))
    monkeypatch.setattr(primitives.screen, "screenshot_diff", lambda a, b: 0.1)


def test_run_click_routes_vision_point(monkeypatch):
    _stub_screen(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        primitives.jinput, "click",
        lambda target, window_hint=None, point=None, expect_name=None, expect_label=None:
            captured.update(point=point, label=expect_label) or
            {"ok": True, "message": "Clicked the element."})
    out = primitives._run_click(
        {"target": "trash", "window": "IconPad"},
        {"vision_point": (160, 260), "vision_label": "delete item"})
    assert captured["point"] == (160, 260) and captured["label"] == "delete item"
    assert out.startswith("OK")


def test_run_click_vision_failed_never_clicks(monkeypatch):
    _stub_screen(monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(primitives.jinput, "click",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    out = primitives._run_click(
        {"target": "ghost", "window": "IconPad"},
        {"vision_failed": "couldn't find it, even visually"})
    assert out.startswith("FAILED") and calls["n"] == 0


def test_vision_destructive_click_walks_confirm(monkeypatch, state_log, confirm_events):
    """Full agent walk: fast path fails → vision destructive → CONFIRM →
    approve → EXECUTING clicks the vision point. The gate is NOT bypassed."""
    _stub_screen(monkeypatch)
    monkeypatch.setattr(primitives.jinput, "resolve_target",
                        lambda d, window_hint=None: {"ok": False, "message": "nf", "candidates": []})
    from jarvis.primitives import vision as _vision
    monkeypatch.setattr(_vision, "locate_and_classify",
                        lambda d, window_hint=None: {"ok": True, "point": (160, 260),
                        "label": "delete item", "tier": "confirm",
                        "window_title": "IconPad", "confidence": 0.9})
    clicked = {}
    monkeypatch.setattr(primitives.jinput, "click",
                        lambda target, window_hint=None, point=None, expect_name=None, expect_label=None:
                        clicked.update(point=point) or {"ok": True, "message": "Clicked."})

    unsubscribe = _auto_resolver(approved=True)
    try:
        provider = ToolCallingProvider(tool_name="click",
                                       tool_args={"target": "the trash icon", "window": "IconPad"})
        brain = _make_brain(provider)
        brain.think("click the trash icon in IconPad")
    finally:
        unsubscribe()

    assert clicked.get("point") == (160, 260), "approved vision click must fire at the vision point"
    states = [e["state"] for e in state_log]
    assert states == ["thinking", "confirming", "executing", "thinking", "idle"]
    assert "delete item" in confirm_events[0]["description"]


def test_vision_safe_click_is_auto_no_modal(monkeypatch, state_log, confirm_events):
    _stub_screen(monkeypatch)
    monkeypatch.setattr(primitives.jinput, "resolve_target",
                        lambda d, window_hint=None: {"ok": False, "message": "nf", "candidates": []})
    from jarvis.primitives import vision as _vision
    monkeypatch.setattr(_vision, "locate_and_classify",
                        lambda d, window_hint=None: {"ok": True, "point": (50, 50),
                        "label": "bold", "tier": "auto", "window_title": "IconPad",
                        "confidence": 0.95})
    monkeypatch.setattr(primitives.jinput, "click",
                        lambda target, window_hint=None, point=None, expect_name=None, expect_label=None:
                        {"ok": True, "message": "Clicked."})
    provider = ToolCallingProvider(tool_name="click",
                                   tool_args={"target": "the bold icon", "window": "IconPad"})
    brain = _make_brain(provider)
    brain.think("click the bold icon")
    states = [e["state"] for e in state_log]
    assert "confirming" not in states and "executing" in states
    assert confirm_events == []
