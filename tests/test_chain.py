"""Slice-6 Stage-1 tests: the chain core — a per-think() ChainTracker that
broadcasts plan/step events through the SAME seq-ordered broadcaster as state
events, enriches the EXECUTING detail with a step counter, and can never leak
a stale chain past think()'s finally (crash included). Parallel tool calls in
one round are tracked per call, in emission order (plan condition #1).
"""
from __future__ import annotations

import pytest

from jarvis import primitives
from jarvis.core import chain
from jarvis.providers.brain.base import BrainProvider, BrainResponse, ToolCall
from jarvis.state import AgentState, StateBroadcaster, broadcaster


@pytest.fixture()
def state_log():
    events: list[dict] = []
    unsubscribe = broadcaster.subscribe(events.append)
    yield events
    unsubscribe()


@pytest.fixture(autouse=True)
def _no_leaked_chain():
    """Every test must end with no current chain AND the global broadcaster
    back at IDLE — a leak here poisons unrelated test files downstream."""
    yield
    assert chain.current() is None, "a test leaked a live ChainTracker"
    assert broadcaster.current is AgentState.IDLE, \
        "a test left the global broadcaster off IDLE"


def _make_brain(provider):
    from jarvis.brain import JarvisBrain
    brain = JarvisBrain()
    brain._provider_override = provider
    return brain


def _stub_launch(monkeypatch, log=None):
    monkeypatch.setitem(
        primitives.PRIMITIVES["launch_app"], "fn",
        lambda args, gi=None: (log.append(args) if log is not None else None)
        or "LAUNCHED. VERIFY: ok.")


class ScriptedProvider(BrainProvider):
    """Plays back a fixed list of BrainResponse rounds (or raises the entry)."""
    supports_tools = True

    def __init__(self, rounds):
        self.script = list(rounds)
        self.calls = 0

    def is_configured(self):
        return True

    def generate(self, messages, system_prompt, tools=None):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ---------- broadcaster.emit shares the seq stream with set() ----------

def test_emit_shares_seq_with_set():
    b = StateBroadcaster()
    received: list[dict] = []
    b.subscribe(received.append)

    b.set(AgentState.THINKING)
    b.emit({"type": "step", "tool": "launch_app", "status": "start"})
    b.set(AgentState.EXECUTING, detail="1 · launch_app")

    assert [e["seq"] for e in received] == [1, 2, 3]
    assert received[1]["type"] == "step"
    # emit() must not disturb the state machine
    assert b.current is AgentState.EXECUTING


# ---------- step events through a real brain walk ----------

def test_chain_step_events_ordered(monkeypatch, state_log):
    """Two sequential tool rounds -> step start/ok pairs with n=1 then n=2,
    interleaved in strict seq order with the state events."""
    _stub_launch(monkeypatch)
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[ToolCall(id="t1", name="launch_app",
                                           args={"name": "notepad"})]),
        BrainResponse(tool_calls=[ToolCall(id="t2", name="launch_app",
                                           args={"name": "calculator"})]),
        BrainResponse(text="Both open, sir."),
    ])
    reply = _make_brain(provider).think("open notepad then calculator")
    assert reply == "Both open, sir."

    steps = [e for e in state_log if e["type"] == "step"]
    assert [(e["n"], e["status"]) for e in steps] == [
        (1, "start"), (1, "ok"), (2, "start"), (2, "ok")]
    assert all(e["tool"] == "launch_app" for e in steps)
    seqs = [e["seq"] for e in state_log]
    assert seqs == sorted(seqs)
    ends = [e for e in state_log if e["type"] == "chain_end"]
    assert [e["status"] for e in ends] == ["done"]


def test_parallel_calls_in_one_round_tracked_per_call(monkeypatch, state_log):
    """Gemini can return SEVERAL tool calls in one round (risk register):
    each is tracked as its own step, in emission order."""
    ran: list[dict] = []
    _stub_launch(monkeypatch, log=ran)
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[
            ToolCall(id="t1", name="launch_app", args={"name": "notepad"}),
            ToolCall(id="t2", name="launch_app", args={"name": "calculator"}),
        ]),
        BrainResponse(text="Done."),
    ])
    _make_brain(provider).think("open notepad and calculator")

    assert [a["name"] for a in ran] == ["notepad", "calculator"]
    steps = [e for e in state_log if e["type"] == "step"]
    assert [(e["n"], e["status"], ) for e in steps] == [
        (1, "start"), (1, "ok"), (2, "start"), (2, "ok")]
    # both calls belong to ONE chain that ends exactly once
    assert len([e for e in state_log if e["type"] == "chain_end"]) == 1


def test_failed_result_marks_step_failed(monkeypatch, state_log):
    monkeypatch.setitem(primitives.PRIMITIVES["launch_app"], "fn",
                        lambda args, gi=None: "FAILED: no such app.")
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[ToolCall(id="t1", name="launch_app",
                                           args={"name": "bogus"})]),
        BrainResponse(text="Couldn't find that app, sir."),
    ])
    _make_brain(provider).think("open bogusapp")
    steps = [e for e in state_log if e["type"] == "step"]
    assert [(e["n"], e["status"]) for e in steps] == [(1, "start"), (1, "failed")]


# ---------- the leak test: crash mid-chain still clears ----------

def test_chain_cleared_on_crash_finally(monkeypatch, state_log):
    """Provider explodes on round 2 (mid-chain). think() must still emit a
    terminal chain_end and leave NO current tracker (state-leak risk)."""
    _stub_launch(monkeypatch)
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[ToolCall(id="t1", name="launch_app",
                                           args={"name": "notepad"})]),
        RuntimeError("provider exploded mid-chain"),
    ])
    reply = _make_brain(provider).think("open notepad then explode")
    assert "went wrong" in reply  # contained, never raised

    ends = [e for e in state_log if e["type"] == "chain_end"]
    assert len(ends) == 1 and ends[0]["status"] == "error"
    assert chain.current() is None
    assert broadcaster.current is AgentState.IDLE


# ---------- EXECUTING detail carries the step counter ----------

def test_executing_detail_shows_step_counter(monkeypatch, state_log):
    _stub_launch(monkeypatch)
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[ToolCall(id="t1", name="launch_app",
                                           args={"name": "notepad"})]),
        BrainResponse(tool_calls=[ToolCall(id="t2", name="launch_app",
                                           args={"name": "calculator"})]),
        BrainResponse(text="Done."),
    ])
    _make_brain(provider).think("open two apps")
    details = [e["detail"] for e in state_log
               if e["type"] == "state" and e["state"] == "executing"]
    # No declared plan yet (plan_steps is Stage 2) -> bare call counter.
    assert details == ["1 · launch_app", "2 · launch_app"]


def test_detail_with_declared_plan_shows_total(monkeypatch, state_log):
    """Tracker-level: once a plan is declared, detail becomes k/N · tool."""
    _stub_launch(monkeypatch)

    tracker = chain.start()
    try:
        tracker.set_plan(["open notepad", "type the note", "save it"])
        assert tracker.detail("launch_app") == "1/3 · launch_app"
        n = tracker.begin_call("launch_app")
        tracker.end_call(n, "ok")  # action ok -> cursor advances
        assert tracker.detail("click") == "2/3 · click"
        n = tracker.begin_call("read_ui_tree")
        tracker.end_call(n, "ok")  # perception -> cursor holds
        assert tracker.detail("click") == "2/3 · click"
    finally:
        chain.clear("done")
    assert [e["status"] for e in state_log if e["type"] == "chain_end"] == ["done"]


def test_direct_execute_without_chain_keeps_plain_detail(monkeypatch, state_log):
    """primitives.execute() outside any think() (no tracker) must behave
    exactly as before — plain tool-name detail."""
    _stub_launch(monkeypatch)
    assert chain.current() is None
    try:
        primitives.execute("launch_app", {"name": "notepad"})
    finally:
        # execute()'s finally parks the state at THINKING (inside think()
        # that's correct — the model round continues). Outside a brain we
        # must restore IDLE or we poison the global broadcaster for every
        # later test (this exact leak broke test_server once).
        broadcaster.set(AgentState.IDLE)
    details = [e["detail"] for e in state_log
               if e["type"] == "state" and e["state"] == "executing"]
    assert details == ["launch_app"]
