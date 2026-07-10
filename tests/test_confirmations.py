"""Slice-3 stage-1 tests: the confirmation gate. The invariant under test
everywhere: NO abnormal path may ever read as approval. Timeout, decline,
supersession, broken subscriber, late/double/unknown resolve — all deny."""
from __future__ import annotations

import threading
import time

import pytest

from jarvis.core.confirmations import ConfirmationManager
from jarvis.state import AgentState


@pytest.fixture()
def manager():
    return ConfirmationManager()


@pytest.fixture()
def events(manager):
    log: list[dict] = []
    manager.subscribe(log.append)
    return log


def _resolve_later(manager, events, approved, delay=0.05):
    def worker():
        deadline = time.time() + 2
        while time.time() < deadline:
            requests = [e for e in events if e["type"] == "confirm_request"]
            if requests:
                time.sleep(delay)
                manager.resolve(requests[0]["id"], approved)
                return
            time.sleep(0.01)
    t = threading.Thread(target=worker)
    t.start()
    return t


def test_approve_path(manager, events):
    t = _resolve_later(manager, events, approved=True)
    decision = manager.request("Delete file 'test.txt'", timeout_s=2)
    t.join()
    assert decision.approved is True
    assert decision.reason == "approved"
    kinds = [e["type"] for e in events]
    assert kinds == ["confirm_request", "confirm_resolved"]
    assert events[0]["description"] == "Delete file 'test.txt'"
    assert events[1]["result"] == "approved"


def test_command_field_present_when_passed(manager, events):
    """Slice 9: an optional verbatim `command` rides the confirm_request +
    replay so the modal can show it in a monospace box."""
    t = _resolve_later(manager, events, approved=False)
    manager.request("Run a shell command", timeout_s=2, command="echo hi & dir")
    t.join()
    req = next(e for e in events if e["type"] == "confirm_request")
    assert req["command"] == "echo hi & dir"


def test_command_field_absent_for_normal_confirms(manager, events):
    """Existing confirms (delete_file, close_tabs, …) must be byte-identical —
    no `command` key when none is passed."""
    t = _resolve_later(manager, events, approved=False)
    manager.request("Delete file 'x.txt'", timeout_s=2)
    t.join()
    req = next(e for e in events if e["type"] == "confirm_request")
    assert "command" not in req


def test_pending_event_carries_command(manager):
    ev = threading.Event()
    holder = {}

    def worker():
        holder["d"] = manager.request("Run a shell command", timeout_s=1,
                                      command="whoami")
    t = threading.Thread(target=worker)
    t.start()
    deadline = time.time() + 1
    while time.time() < deadline and manager.pending_event() is None:
        time.sleep(0.01)
    pend = manager.pending_event()
    assert pend is not None and pend["command"] == "whoami"
    # let it resolve/expire cleanly
    t.join()


def test_decline_path(manager, events):
    t = _resolve_later(manager, events, approved=False)
    decision = manager.request("Close window 'Notepad'", timeout_s=2)
    t.join()
    assert decision.approved is False
    assert decision.reason == "declined"
    assert events[-1]["result"] == "declined"


def test_timeout_fails_safe(manager, events):
    executed = []
    decision = manager.request("Dangerous thing", timeout_s=0.3)
    if decision.approved:  # the guard every caller uses
        executed.append("ran")
    assert decision.approved is False
    assert decision.reason == "timeout"
    assert executed == []  # provably nothing ran
    assert events[-1] == {"type": "confirm_resolved",
                          "id": events[0]["id"], "result": "timeout"}


def test_single_flight_second_request_superseded(manager, events):
    results = {}

    def first():
        results["first"] = manager.request("first action", timeout_s=1.5)

    t = threading.Thread(target=first)
    t.start()
    # wait until the first is actually pending
    deadline = time.time() + 1
    while time.time() < deadline and manager.pending_event() is None:
        time.sleep(0.01)

    t0 = time.time()
    second = manager.request("second action", timeout_s=5)
    elapsed = time.time() - t0
    assert second.approved is False
    assert second.reason == "superseded"
    assert elapsed < 0.2, "superseded request must return immediately, not queue"

    manager.resolve(events[0]["id"], True)
    t.join()
    assert results["first"].approved is True
    # exactly ONE modal was ever requested
    assert [e["type"] for e in events].count("confirm_request") == 1


def test_late_resolve_after_timeout_is_noop(manager, events):
    decision = manager.request("thing", timeout_s=0.2)
    assert decision.reason == "timeout"
    assert manager.resolve(events[0]["id"], True) is False
    assert manager.pending_event() is None


def test_double_resolve_second_is_noop(manager, events):
    t = _resolve_later(manager, events, approved=False)
    decision = manager.request("thing", timeout_s=2)
    t.join()
    assert decision.reason == "declined"
    assert manager.resolve(events[0]["id"], True) is False  # too late to flip


def test_unknown_id_resolve_is_noop(manager):
    assert manager.resolve("no-such-id", True) is False


def test_broken_subscriber_fails_closed(manager):
    def broken(_event):
        raise RuntimeError("modal pipeline down")
    manager.subscribe(broken)
    decision = manager.request("thing", timeout_s=5)
    assert decision.approved is False
    assert decision.reason == "error"


def test_pending_event_replay_for_reconnect(manager, events):
    t = _resolve_later(manager, events, approved=True, delay=0.3)
    result = {}

    def requester():
        result["d"] = manager.request("replayable", timeout_s=2)

    rt = threading.Thread(target=requester)
    rt.start()
    deadline = time.time() + 1
    while time.time() < deadline and manager.pending_event() is None:
        time.sleep(0.01)
    replay = manager.pending_event()
    assert replay and replay["type"] == "confirm_request"
    assert replay["description"] == "replayable"
    rt.join(); t.join()
    assert manager.pending_event() is None  # cleared after resolution


def test_confirming_state_exists():
    assert AgentState.CONFIRMING.value == "confirming"
