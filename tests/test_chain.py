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
    # step events carry a compact args summary (Action Log's future feed —
    # and the only way to diagnose live runs: WHAT did it click?)
    assert steps[0]["args"] == "name=notepad"
    assert steps[2]["args"] == "name=calculator"
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


def test_step_end_event_carries_note(monkeypatch, state_log):
    """Slice 7: the step-END event carries a compact verdict note (the
    Action Log row's evidence) — additive field, sourced from the tool
    result, truncated."""
    monkeypatch.setitem(
        primitives.PRIMITIVES["launch_app"], "fn",
        lambda args, gi=None: "Launched notepad.exe (pid 1). VERIFY [VERIFIED]: "
                              + "x" * 200)
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[ToolCall(id="t1", name="launch_app",
                                           args={"name": "notepad"})]),
        BrainResponse(text="Done."),
    ])
    _make_brain(provider).think("open notepad")
    steps = [e for e in state_log if e["type"] == "step"]
    start, end = steps
    assert start["status"] == "start" and end["status"] == "ok"
    assert "note" in end
    assert end["note"].startswith("Launched notepad.exe")
    assert len(end["note"]) <= 91  # 90 + ellipsis


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


# ---------- Stage 2: plan_steps meta-tool ----------

def test_plan_steps_broadcasts_plan_event(monkeypatch, state_log):
    _stub_launch(monkeypatch)
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[ToolCall(id="p1", name="plan_steps",
                                           args={"steps": ["open notepad", "type the note"]})]),
        BrainResponse(tool_calls=[ToolCall(id="t1", name="launch_app",
                                           args={"name": "notepad"})]),
        BrainResponse(text="On it, sir."),
    ])
    _make_brain(provider).think("make a note")

    plans = [e for e in state_log if e["type"] == "plan"]
    assert len(plans) == 1
    assert plans[0]["steps"] == ["open notepad", "type the note"]
    assert plans[0]["revision"] == 1
    # once a plan exists, action step events carry step/total
    action_steps = [e for e in state_log
                    if e["type"] == "step" and e["tool"] == "launch_app"]
    assert all(e["total"] == 2 for e in action_steps)


def test_plan_revision_event(monkeypatch, state_log):
    _stub_launch(monkeypatch)
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[ToolCall(id="p1", name="plan_steps",
                                           args={"steps": ["a", "b"]})]),
        BrainResponse(tool_calls=[ToolCall(id="p2", name="plan_steps",
                                           args={"steps": ["a", "b2", "c"]})]),
        BrainResponse(text="Revised."),
    ])
    _make_brain(provider).think("do the thing")
    plans = [e for e in state_log if e["type"] == "plan"]
    assert [p["revision"] for p in plans] == [1, 2]
    assert plans[1]["steps"] == ["a", "b2", "c"]


def test_unplanned_chain_tracks_with_null_total(monkeypatch, state_log):
    """No plan_steps call -> chain still tracked honestly, total=None."""
    _stub_launch(monkeypatch)
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[ToolCall(id="t1", name="launch_app",
                                           args={"name": "notepad"})]),
        BrainResponse(text="Open."),
    ])
    _make_brain(provider).think("open notepad")
    steps = [e for e in state_log if e["type"] == "step"]
    assert steps and all(e["total"] is None and e["step"] is None for e in steps)


def test_plan_steps_not_in_primitives_registry():
    """plan_steps is a brain-level meta-tool: exposed in the brain's schema,
    absent from the OS-facing primitives registry (no gate/verify wrapper)."""
    from jarvis.brain import JarvisBrain
    assert "plan_steps" not in primitives.PRIMITIVES
    assert "plan_steps" in [t["name"] for t in JarvisBrain().tools()]


def test_plan_steps_with_action_same_round_applies_first(monkeypatch, state_log):
    """Risk register (plan condition #1): Gemini may return plan_steps AND an
    action in ONE round — the plan must apply before the action executes, so
    the action's EXECUTING detail and step events already carry /N."""
    _stub_launch(monkeypatch)
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[
            ToolCall(id="p1", name="plan_steps",
                     args={"steps": ["open notepad", "type"]}),
            ToolCall(id="t1", name="launch_app", args={"name": "notepad"}),
        ]),
        BrainResponse(text="Going."),
    ])
    _make_brain(provider).think("make a note")

    details = [e["detail"] for e in state_log
               if e["type"] == "state" and e["state"] == "executing"]
    assert details == ["1/2 · launch_app"]
    action_steps = [e for e in state_log
                    if e["type"] == "step" and e["tool"] == "launch_app"]
    assert [(e["step"], e["total"]) for e in action_steps] == [(1, 2), (2, 2)]


def test_plan_steps_empty_is_failed_not_crash(state_log):
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[ToolCall(id="p1", name="plan_steps",
                                           args={"steps": []})]),
        BrainResponse(text="Hmm."),
    ])
    _make_brain(provider).think("plan nothing")
    assert [e for e in state_log if e["type"] == "plan"] == []
    steps = [e for e in state_log if e["type"] == "step"]
    assert [e["status"] for e in steps] == ["start", "failed"]


# ---------- Stage 3: loop guards (hostile) ----------

import threading
import time as _time

from jarvis.core.confirmations import confirmations
from jarvis.primitives import files


def _auto_resolver(approved: bool):
    """Answer the next confirm_request like a user clicking a modal button."""
    def responder(event):
        if event.get("type") == "confirm_request":
            threading.Thread(
                target=lambda: (_time.sleep(0.05),
                                confirmations.resolve(event["id"], approved)),
            ).start()
    return confirmations.subscribe(responder)


@pytest.fixture()
def workspace_file():
    files.AGENT_FILES_DIR.mkdir(parents=True, exist_ok=True)
    f = files.AGENT_FILES_DIR / "chain-gate.txt"
    f.write_text("chain", encoding="utf-8")
    yield f
    f.unlink(missing_ok=True)


@pytest.fixture()
def confirm_events():
    log: list[dict] = []
    unsubscribe = confirmations.subscribe(log.append)
    yield log
    unsubscribe()


def test_chain_midplan_run_shell_needs_own_confirm_no_leak(monkeypatch, state_log,
                                                           confirm_events, workspace_file):
    """Slice 9: a run_shell mid-chain must get its OWN confirm at the moment it
    runs — a chain whose earlier step was approved MUST NOT pre-authorize it.
    Resolver approves the delete but declines the shell; shell must not run."""
    from jarvis.primitives import shell
    ran = []
    monkeypatch.setattr(shell, "run_shell",
                        lambda cmd: ran.append(cmd) or {"ok": True, "message": "x",
                                                        "exit_code": 0, "stdout": "",
                                                        "stderr": ""})

    def selective(event):
        if event.get("type") == "confirm_request":
            approve = "delete" in event["description"].lower()  # yes to delete, no to shell
            threading.Thread(target=lambda: (_time.sleep(0.05),
                             confirmations.resolve(event["id"], approve))).start()
    unsub = confirmations.subscribe(selective)
    try:
        provider = ScriptedProvider([
            BrainResponse(tool_calls=[ToolCall(id="p", name="plan_steps",
                          args={"steps": ["delete the file", "run a shell command"]})]),
            BrainResponse(tool_calls=[ToolCall(id="t1", name="delete_file",
                          args={"name": "chain-gate.txt"})]),
            BrainResponse(tool_calls=[ToolCall(id="t2", name="run_shell",
                          args={"command": "echo should-not-run"})]),
            BrainResponse(text="Deleted the file; you declined the shell command, sir."),
        ])
        brain = _make_brain(provider)
        brain.think("delete the file then run a shell command")
    finally:
        unsub()

    reqs = [e for e in confirm_events if e["type"] == "confirm_request"]
    assert len(reqs) == 2, "each gated step must raise its OWN modal (no leak)"
    assert any(r.get("command") == "echo should-not-run" for r in reqs), \
        "run_shell must raise its own confirm carrying the verbatim command"
    assert not workspace_file.exists(), "the approved delete should have happened"
    assert ran == [], "the declined shell command must NEVER execute"
    shell_result = next(m["content"] for m in reversed(brain.history)
                        if m["role"] == "tool" and m["name"] == "run_shell")
    assert "CANCELLED" in shell_result


def test_approve_everything_still_cannot_run_denylisted(monkeypatch, state_log,
                                                        confirm_events):
    """Hostile: a user (auto-resolver) that approves EVERYTHING still cannot
    run a denylisted command — the denylist sits BENEATH approval, refusing
    before any modal is shown."""
    from jarvis.primitives import shell
    monkeypatch.setattr(shell, "run_shell",
                        lambda cmd: (_ for _ in ()).throw(
                            AssertionError("denylisted command must never execute")))
    unsub = _auto_resolver(approved=True)  # approve anything that asks
    try:
        provider = ScriptedProvider([
            BrainResponse(tool_calls=[ToolCall(id="t1", name="run_shell",
                          args={"command": "rm -rf /"})]),
            BrainResponse(text="I refused that one, sir."),
        ])
        brain = _make_brain(provider)
        brain.think("wipe the system drive")
    finally:
        unsub()

    result = next(m["content"] for m in reversed(brain.history)
                  if m["role"] == "tool" and m["name"] == "run_shell")
    assert result.startswith("BLOCKED"), result
    assert [e for e in confirm_events if e["type"] == "confirm_request"] == [], \
        "a denylisted command must never even reach the approval modal"


def test_identical_retry_after_failure_is_blocked_not_executed(monkeypatch, state_log):
    """The breaker: an exact (tool, args) repeat of a just-FAILED call must
    NOT reach the primitive — the model gets a synthetic BLOCKED result."""
    ran: list[dict] = []
    monkeypatch.setitem(primitives.PRIMITIVES["launch_app"], "fn",
                        lambda args, gi=None: ran.append(args) or "FAILED: no such app.")
    same = ToolCall(id="t1", name="launch_app", args={"name": "bogus"})
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[same]),
        BrainResponse(tool_calls=[ToolCall(id="t2", name="launch_app",
                                           args={"name": "bogus"})]),
        BrainResponse(text="Understood, changing approach."),
    ])
    brain = _make_brain(provider)
    brain.think("open bogusapp")

    assert len(ran) == 1, "the identical retry must never execute"
    blocked = [m for m in brain.history
               if m["role"] == "tool" and m["content"].startswith("BLOCKED")]
    assert len(blocked) == 1
    assert "read_ui_tree" in blocked[0]["content"]  # told how to proceed
    # a blocked call never ran -> no step events for it
    steps = [e for e in state_log if e["type"] == "step"]
    assert [(e["n"], e["status"]) for e in steps] == [(1, "start"), (1, "failed")]


def test_changed_args_after_failure_is_allowed(monkeypatch, state_log):
    """The breaker blocks REPEATS, not corrections — different args run."""
    ran: list[dict] = []
    monkeypatch.setitem(
        primitives.PRIMITIVES["launch_app"], "fn",
        lambda args, gi=None: ran.append(args) or
        ("FAILED: no such app." if args["name"] == "bogus" else "LAUNCHED. VERIFY: ok."))
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[ToolCall(id="t1", name="launch_app",
                                           args={"name": "bogus"})]),
        BrainResponse(tool_calls=[ToolCall(id="t2", name="launch_app",
                                           args={"name": "notepad"})]),
        BrainResponse(text="Got it open on the second try, sir."),
    ])
    _make_brain(provider).think("open the editor")
    assert [a["name"] for a in ran] == ["bogus", "notepad"]


def test_three_failures_abort_chain_with_honest_summary(monkeypatch, state_log):
    """Failure budget: the third hard failure aborts the chain — later calls
    get a synthetic ABORTED result and never execute."""
    ran: list[dict] = []
    monkeypatch.setitem(primitives.PRIMITIVES["launch_app"], "fn",
                        lambda args, gi=None: ran.append(args) or "FAILED: nope.")
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[ToolCall(id="t1", name="launch_app", args={"name": "a"})]),
        BrainResponse(tool_calls=[ToolCall(id="t2", name="launch_app", args={"name": "b"})]),
        BrainResponse(tool_calls=[ToolCall(id="t3", name="launch_app", args={"name": "c"})]),
        BrainResponse(tool_calls=[ToolCall(id="t4", name="launch_app", args={"name": "d"})]),
        BrainResponse(text="I couldn't get any of those open, sir."),
    ])
    brain = _make_brain(provider)
    brain.think("open all the things")

    assert [a["name"] for a in ran] == ["a", "b", "c"], "4th call must not run"
    aborted = [m for m in brain.history
               if m["role"] == "tool" and "CHAIN ABORTED" in m["content"]]
    assert len(aborted) == 1 and "failures" in aborted[0]["content"]
    ends = [e for e in state_log if e["type"] == "chain_end"]
    assert [e["status"] for e in ends] == ["budget"]


def test_step2_of_3_fails_replan_or_clean_fail_never_forever(monkeypatch, state_log):
    """THE named hostile case: plan [A, B, C]; A ok; B fails; identical B
    blocked; revised B' fails; third failure -> budget abort; C never runs.
    Loop-forever impossible: breaker + budget + MAX_TOOL_ROUNDS."""
    from jarvis.brain import MAX_TOOL_ROUNDS
    ran: list[dict] = []
    monkeypatch.setitem(
        primitives.PRIMITIVES["launch_app"], "fn",
        lambda args, gi=None: ran.append(args) or
        ("LAUNCHED. VERIFY: ok." if args["name"] == "notepad" else "FAILED: no window."))
    b_call = {"name": "broken-app"}
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[ToolCall(id="p1", name="plan_steps",
                                           args={"steps": ["A open notepad",
                                                           "B open broken-app",
                                                           "C read the screen"]})]),
        BrainResponse(tool_calls=[ToolCall(id="t1", name="launch_app", args={"name": "notepad"})]),
        BrainResponse(tool_calls=[ToolCall(id="t2", name="launch_app", args=dict(b_call))]),   # B fails
        BrainResponse(tool_calls=[ToolCall(id="t3", name="launch_app", args=dict(b_call))]),   # blind retry -> BLOCKED
        BrainResponse(tool_calls=[ToolCall(id="p2", name="plan_steps",                          # replan (visible)
                                           args={"steps": ["A done", "B try variant", "C read"]}),
                                  ToolCall(id="t4", name="launch_app", args={"name": "broken-app-2"})]),  # fails (2)
        BrainResponse(tool_calls=[ToolCall(id="t5", name="launch_app", args={"name": "broken-app-3"})]),  # fails (3) -> budget
        BrainResponse(tool_calls=[ToolCall(id="t6", name="read_ui_tree", args={})]),            # after abort -> synthetic
        BrainResponse(text="Sir: notepad opened; the second app failed three ways; I stopped there."),
    ])
    brain = _make_brain(provider)
    reply = brain.think("open notepad and broken-app then read the screen")

    assert provider.calls <= MAX_TOOL_ROUNDS, "must terminate inside the round cap"
    assert reply.startswith("Sir:"), "reached final prose — no infinite loop"
    # B's blind retry never executed; C (read_ui_tree) never executed after abort
    assert [a["name"] for a in ran] == ["notepad", "broken-app", "broken-app-2", "broken-app-3"]
    plans = [e for e in state_log if e["type"] == "plan"]
    assert [p["revision"] for p in plans] == [1, 2], "the replan is a visible event"
    ends = [e for e in state_log if e["type"] == "chain_end"]
    assert [e["status"] for e in ends] == ["budget"]


def test_cancelled_midchain_blocks_rest_mechanically(monkeypatch, state_log, workspace_file):
    """User declines step 2's CONFIRM -> step 3 must never execute, enforced
    by the brain (synthetic ABORTED result), not by trusting the model."""
    ran: list[dict] = []
    monkeypatch.setitem(primitives.PRIMITIVES["launch_app"], "fn",
                        lambda args, gi=None: ran.append(args) or "LAUNCHED. VERIFY: ok.")
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[ToolCall(id="t1", name="launch_app", args={"name": "notepad"})]),
        BrainResponse(tool_calls=[ToolCall(id="t2", name="delete_file",
                                           args={"name": "chain-gate.txt"})]),
        BrainResponse(tool_calls=[ToolCall(id="t3", name="launch_app", args={"name": "calculator"})]),
        BrainResponse(text="Stopping there as you declined, sir."),
    ])
    unsubscribe = _auto_resolver(approved=False)
    try:
        brain = _make_brain(provider)
        brain.think("open notepad, delete the file, open calculator")
    finally:
        unsubscribe()

    assert workspace_file.exists(), "declined deletion must not happen"
    assert [a["name"] for a in ran] == ["notepad"], "step 3 must never run after decline"
    aborted = [m for m in brain.history
               if m["role"] == "tool" and "CHAIN ABORTED" in m["content"]]
    assert len(aborted) == 1
    ends = [e for e in state_log if e["type"] == "chain_end"]
    assert [e["status"] for e in ends] == ["cancelled"]


def test_confirm_approved_midchain_resumes(monkeypatch, state_log, workspace_file):
    """Approval mid-chain resumes the chain exactly where it paused."""
    ran: list[dict] = []
    monkeypatch.setitem(primitives.PRIMITIVES["launch_app"], "fn",
                        lambda args, gi=None: ran.append(args) or "LAUNCHED. VERIFY: ok.")
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[ToolCall(id="t1", name="launch_app", args={"name": "notepad"})]),
        BrainResponse(tool_calls=[ToolCall(id="t2", name="delete_file",
                                           args={"name": "chain-gate.txt"})]),
        BrainResponse(tool_calls=[ToolCall(id="t3", name="launch_app", args={"name": "calculator"})]),
        BrainResponse(text="All three done, sir."),
    ])
    unsubscribe = _auto_resolver(approved=True)
    try:
        _make_brain(provider).think("open notepad, delete the file, open calculator")
    finally:
        unsubscribe()

    assert not workspace_file.exists(), "approved deletion must happen"
    assert [a["name"] for a in ran] == ["notepad", "calculator"], "chain resumed after approval"
    states = [e["state"] for e in state_log if e["type"] == "state"]
    assert "confirming" in states
    ends = [e for e in state_log if e["type"] == "chain_end"]
    assert [e["status"] for e in ends] == ["done"]


def test_round_exhaustion_reports_chain_status(monkeypatch, state_log):
    """Exhausting MAX_TOOL_ROUNDS must produce an honest progress report,
    not the old generic 'I got stuck'."""
    from jarvis.brain import MAX_TOOL_ROUNDS
    _stub_launch(monkeypatch)
    provider = ScriptedProvider([
        BrainResponse(tool_calls=[ToolCall(id=f"t{i}", name="launch_app",
                                           args={"name": f"app{i}"})])
        for i in range(MAX_TOOL_ROUNDS)
    ])
    reply = _make_brain(provider).think("open everything")
    assert "action limit" in reply
    assert f"{MAX_TOOL_ROUNDS} succeeded" in reply
    ends = [e for e in state_log if e["type"] == "chain_end"]
    assert [e["status"] for e in ends] == ["exhausted"]


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
