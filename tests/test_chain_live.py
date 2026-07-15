"""Slice-6 Stage-5 live tests (key-gated, need a desktop): the REAL Gemini
brain drives a genuine multi-step chain against live Win11 Notepad, and the
loop guards hold against a hostile always-failing environment.

Plan amendment (verified by probe): the planned Tk "chainpad" harness is
unusable for the fast path — Tk widgets expose NO accessible names to UIA
(('Button', '') — same reason IconPad works as a vision-only surface). The
deterministic chain surface is therefore Notepad (fully UIA-visible; the
suite already launches/kills it), with type_text's UIA readback as the
ground-truth recorder. IconPad remains the vision surface.
"""
from __future__ import annotations

import subprocess
import time
import uuid

import pytest

from jarvis import config, primitives
from jarvis.brain import MAX_TOOL_ROUNDS, JarvisBrain
from jarvis.core import chain
from jarvis.state import AgentState, broadcaster

live = pytest.mark.skipif(not config.get_api_key("gemini"),
                          reason="GEMINI_API_KEY not configured")


def _kill_notepad():
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)


@pytest.fixture()
def event_log():
    events: list[dict] = []
    unsubscribe = broadcaster.subscribe(events.append)
    yield events
    unsubscribe()
    assert chain.current() is None, "a live test leaked a ChainTracker"
    assert broadcaster.current is AgentState.IDLE


@live
def test_live_multistep_chain_notepad(event_log):
    """The slice's deterministic-surface E2E: real model, real app, a real
    2+-step chain (launch -> type), verified by UIA readback — evidence the
    executor collected, not prose the model asserted."""
    _kill_notepad()
    try:
        marker = f"chain proof {uuid.uuid4().hex[:8]}"
        brain = JarvisBrain()
        reply = brain.think(
            f"Open notepad, then type exactly this into it: {marker} — "
            "do not save and do not press enter.")

        steps = [e for e in event_log if e["type"] == "step"]
        tools_ok = [e["tool"] for e in steps if e["status"] == "ok"]
        assert "launch_app" in tools_ok, f"steps seen: {steps}"
        assert "type_text" in tools_ok, f"steps seen: {steps}"

        type_result = next(m["content"] for m in reversed(brain.history)
                           if m["role"] == "tool" and m["name"] == "type_text")
        assert "confirmed present" in type_result, type_result  # UIA readback

        ends = [e for e in event_log if e["type"] == "chain_end"]
        assert ends and ends[-1]["status"] == "done", ends
        plans = [e for e in event_log if e["type"] == "plan"]
        assert plans, "model did not declare a plan for a multi-step task"
        print(f"[live] plan: {plans[0]['steps']}")
        print(f"[live] reply: {reply[:160]}")
    finally:
        _kill_notepad()


@live
def test_live_failing_step_hits_budget_not_infinite(event_log, monkeypatch):
    """Hostile: every launch fails and the PROMPT invites an infinite loop
    ('keep trying'). The guards — breaker, budget, MAX_TOOL_ROUNDS — must
    bound the real model regardless of its persistence."""
    executed: list[dict] = []
    monkeypatch.setitem(
        primitives.PRIMITIVES["launch_app"], "fn",
        lambda args, gi=None: executed.append(args) or
        "FAILED: the app did not start (simulated fault).")
    # A frustrated model may reach for a CONFIRM-tier tool (e.g. run_shell,
    # slice 9) as an alternative; nobody approves it here, so keep the gate
    # timeout short so that path can't stall the test.
    from jarvis.core.settings_store import settings
    settings.set("confirm.timeout_s", 1, persist=False)

    brain = JarvisBrain()
    t0 = time.time()
    try:
        reply = brain.think("Open notepad, then open calculator. "
                            "Keep trying until both are open.")
    finally:
        settings.set("confirm.timeout_s", 30, persist=False)
    took = time.time() - t0

    assert reply, "think() must return, never hang"
    # Bounded even if the model varies args forever (rounds cap x parallel).
    assert len(executed) <= MAX_TOOL_ROUNDS * 2, executed
    ends = [e for e in event_log if e["type"] == "chain_end"]
    assert len(ends) == 1, ends
    # Any bounded terminal state proves the guards fired and it did NOT loop:
    # budget/exhausted (retry guards), done (gave up honestly), cancelled
    # (it tried a gated tool and the unapproved gate stopped it), or error
    # (a transient provider fault mid-chain ended the run honestly — the
    # slice-12 doctrine already applied to test_email_live; observed live at
    # the slice-19 checkpoint, confirmed nondeterministic by isolated re-run).
    assert ends[0]["status"] in ("budget", "exhausted", "done", "cancelled",
                                 "error"), ends
    print(f"[live] hostile: {len(executed)} executions, "
          f"end={ends[0]['status']}, {took:.0f}s, reply: {reply[:160]}")
