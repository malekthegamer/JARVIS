"""Slice 49 — barge-in: cutting JARVIS off mid-sentence.

ONE cancel path, two triggers (the HUD control and the wake word). These tests
pin the mechanism and the two properties that would be dangerous if wrong:

  * it must NEVER touch the _busy lock -- a stop that waited on the lock held by
    the very interaction it is cancelling would deadlock forever
  * stopping must prevent the NEXT step, and say so honestly; a step already in
    flight cannot be un-fired and nothing here pretends otherwise
"""
from __future__ import annotations

import pytest

from jarvis.core import chain, interrupt


@pytest.fixture(autouse=True)
def _clean_chain():
    """No chain OR broadcaster leaks between tests.

    The state-leak pattern, now four slices running (43 browser settings, 44
    brain model, 45 the SDK patch, 48 the broadcaster). `_wake_env` below drives
    the broadcaster into SPEAKING/EXECUTING, which is process-wide state; left
    behind it corrupts any later test that asserts on the current state. The
    restoring fixture is written in the same edit as the thing that mutates.
    """
    from jarvis.state import AgentState, broadcaster
    chain.clear("done")
    yield
    chain.clear("done")
    broadcaster.set(AgentState.IDLE)


# ---------------------------------------------------------------- mechanism
def test_request_stops_playback(monkeypatch):
    stopped = []
    monkeypatch.setattr(interrupt.playback, "stop", lambda: stopped.append(True))
    chain.start()
    interrupt.request()
    assert stopped, "the audio must actually be told to stop"


def test_request_marks_the_current_chain_aborted(monkeypatch):
    monkeypatch.setattr(interrupt.playback, "stop", lambda: None)
    tracker = chain.start()
    assert interrupt.request() is True
    assert tracker.aborted == "interrupted", tracker.aborted


def test_aborted_chain_refuses_every_later_step(monkeypatch):
    """Reuses the PROVEN mechanism: pre_call_guard already refuses when
    aborted is set, so barge-in sets a flag rather than inventing machinery."""
    monkeypatch.setattr(interrupt.playback, "stop", lambda: None)
    tracker = chain.start()
    interrupt.request()
    blocked = tracker.pre_call_guard("launch_app", {"name": "notepad"})
    assert blocked is not None, "an interrupted chain must refuse the next step"
    assert "ABORTED" in blocked


def test_the_refusal_message_says_it_was_interrupted(monkeypatch):
    """Not 'declined a confirmation', not 'too many failures' -- the model must
    tell the user the TRUE reason the rest did not run."""
    monkeypatch.setattr(interrupt.playback, "stop", lambda: None)
    tracker = chain.start()
    interrupt.request()
    msg = tracker.pre_call_guard("launch_app", {})
    assert "interrupt" in msg.lower(), msg
    assert "declined" not in msg.lower(), f"wrong reason reported: {msg}"
    assert "failures" not in msg.lower(), f"wrong reason reported: {msg}"


def test_request_with_nothing_running_is_a_harmless_noop(monkeypatch):
    monkeypatch.setattr(interrupt.playback, "stop", lambda: None)
    chain.clear("done")
    assert interrupt.request() is False, "nothing to interrupt = False, not an error"


def test_a_second_request_is_a_noop(monkeypatch):
    monkeypatch.setattr(interrupt.playback, "stop", lambda: None)
    chain.start()
    assert interrupt.request() is True
    assert interrupt.request() is False, "already interrupted -- nothing more to do"


def test_request_never_raises_when_audio_is_unavailable(monkeypatch):
    """No speakers, no pygame, a wedged mixer -- the chain must still abort."""
    def boom():
        raise RuntimeError("no audio device")
    monkeypatch.setattr(interrupt.playback, "stop", boom)
    tracker = chain.start()
    assert interrupt.request() is True, "audio failure must not stop the abort"
    assert tracker.aborted == "interrupted"


def test_request_stops_audio_even_with_no_chain(monkeypatch):
    """Reading a long answer with no tools running is the commonest case."""
    stopped = []
    monkeypatch.setattr(interrupt.playback, "stop", lambda: stopped.append(True))
    chain.clear("done")
    interrupt.request()
    assert stopped, "audio must stop even when no chain is running"


# ---------------------------------------------------------------- honesty
def test_interrupted_chain_reports_what_did_and_did_not_run(monkeypatch):
    monkeypatch.setattr(interrupt.playback, "stop", lambda: None)
    tracker = chain.start()
    n = tracker.begin_call("launch_app", {"name": "notepad"})
    tracker.end_call(n, "ok", note="OK: opened")
    interrupt.request()
    msg = tracker.pre_call_guard("set_volume", {"level": 20})
    assert msg is not None
    assert "which steps" in msg.lower() or "completed" in msg.lower(), msg


# ==================== stage 2: the HUD trigger ====================

def test_api_stop_endpoint_interrupts(monkeypatch):
    from fastapi.testclient import TestClient
    from jarvis import server
    monkeypatch.setattr(interrupt.playback, "stop", lambda: None)
    tracker = chain.start()
    with TestClient(server.app) as c:
        r = c.post("/api/stop")
    assert r.status_code == 200, r.text
    assert r.json()["interrupted"] is True
    assert tracker.aborted == "interrupted"


def test_api_stop_with_nothing_running_is_still_ok(monkeypatch):
    from fastapi.testclient import TestClient
    from jarvis import server
    monkeypatch.setattr(interrupt.playback, "stop", lambda: None)
    chain.clear("done")
    with TestClient(server.app) as c:
        r = c.post("/api/stop")
    assert r.status_code == 200, "pressing stop when idle is not an error"
    assert r.json()["interrupted"] is False


def test_stop_does_not_acquire_the_busy_lock(monkeypatch):
    """RISK 2 -- the whole point. A stop that waited on _busy could never cancel
    the interaction holding it; it would hang until the thing it was cancelling
    finished on its own."""
    from fastapi.testclient import TestClient
    from jarvis import server
    monkeypatch.setattr(interrupt.playback, "stop", lambda: None)
    tracker = chain.start()
    assert server._busy.acquire(blocking=False), "precondition: lock is free"
    try:
        with TestClient(server.app) as c:      # _busy is HELD for this call
            r = c.post("/api/stop")
        assert r.status_code == 200, "stop must work while an interaction holds _busy"
        assert tracker.aborted == "interrupted"
    finally:
        server._busy.release()


def test_ws_stop_message_interrupts(monkeypatch):
    from fastapi.testclient import TestClient
    from jarvis import server
    monkeypatch.setattr(interrupt.playback, "stop", lambda: None)
    tracker = chain.start()
    with TestClient(server.app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.send_json({"type": "stop"})
            # drain until the socket has certainly processed it
            for _ in range(6):
                try:
                    ws.receive_json()
                except Exception:
                    break
                if tracker.aborted:
                    break
    assert tracker.aborted == "interrupted", "a WS stop must abort the chain"


# ==================== stage 3: the voice trigger ====================
# Stage 0 MEASURED that JARVIS's own TTS peaks at 0.196 against a 0.50 threshold
# (381 frames, 0 trips), so listening while speaking will not make it interrupt
# itself. That measurement is what permits this stage to exist.

def _wake_env(monkeypatch, state):
    """Put the server in `state` with _busy held, as during a real interaction."""
    from jarvis import server
    from jarvis.state import broadcaster
    monkeypatch.setattr(interrupt.playback, "stop", lambda: None)
    broadcaster.set(state)
    got = server._busy.acquire(blocking=False)
    assert got, "precondition: _busy free before the test takes it"
    return server


@pytest.mark.parametrize("state_name", ["SPEAKING", "EXECUTING"])
def test_wake_while_busy_interrupts_instead_of_being_dropped(monkeypatch, state_name):
    from jarvis.state import AgentState
    server = _wake_env(monkeypatch, getattr(AgentState, state_name))
    tracker = chain.start()
    try:
        server._on_wake()
    finally:
        server._busy.release()
    assert tracker.aborted == "interrupted", \
        f"a wake during {state_name} must interrupt, not be dropped"


def test_barge_in_does_not_start_a_second_interaction(monkeypatch):
    """RISK 5 -- _busy exists so triggers never stack. Barge-in stops and
    RETURNS; it must not capture a follow-up utterance."""
    from jarvis.state import AgentState
    from jarvis.voice import wake as wake_mod
    server = _wake_env(monkeypatch, AgentState.SPEAKING)
    called = []
    monkeypatch.setattr(wake_mod, "handle_wake",
                        lambda **kw: called.append(True))
    chain.start()
    try:
        server._on_wake()
    finally:
        server._busy.release()
    assert not called, "barge-in must NOT run a second interaction"


def test_wake_while_confirming_does_nothing(monkeypatch):
    """The CONFIRM modal owns its own answer (Approve/Cancel). A wake there must
    not cancel it out from under the user."""
    from jarvis.state import AgentState
    server = _wake_env(monkeypatch, AgentState.CONFIRMING)
    tracker = chain.start()
    try:
        server._on_wake()
    finally:
        server._busy.release()
    assert tracker.aborted is None, "a wake during CONFIRMING must be dropped"


def test_wake_when_idle_still_behaves_normally(monkeypatch):
    """No regression: with _busy free, a wake runs the normal follow-up path."""
    from jarvis.state import AgentState, broadcaster
    from jarvis import server
    from jarvis.voice import wake as wake_mod
    monkeypatch.setattr(interrupt.playback, "stop", lambda: None)
    broadcaster.set(AgentState.IDLE)
    called = []
    monkeypatch.setattr(wake_mod, "handle_wake", lambda **kw: called.append(True))
    server._on_wake()
    assert called, "an idle wake must still start a normal interaction"
