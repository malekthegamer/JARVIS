"""Stage-1 exit tests for the state broadcaster: ordered delivery under
concurrency, unsubscribe actually detaches, one bad subscriber can't break
the rest."""
from __future__ import annotations

import threading

from jarvis.state import AgentState, StateBroadcaster


def test_events_are_ordered_and_sequential():
    b = StateBroadcaster()
    received: list[dict] = []
    b.subscribe(received.append)

    states = [AgentState.LISTENING, AgentState.THINKING, AgentState.SPEAKING, AgentState.IDLE]
    for s in states:
        b.set(s)

    assert [e["state"] for e in received] == [s.value for s in states]
    assert [e["seq"] for e in received] == [1, 2, 3, 4]
    assert b.current is AgentState.IDLE


def test_concurrent_setters_never_duplicate_or_skip_seq():
    b = StateBroadcaster()
    received: list[dict] = []
    b.subscribe(received.append)

    def hammer():
        for _ in range(50):
            b.set(AgentState.THINKING)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    seqs = [e["seq"] for e in received]
    assert len(seqs) == 400
    # Delivery happens under the broadcaster lock, so the received order
    # must be exactly the seq order with no gaps or duplicates.
    assert seqs == list(range(1, 401))


def test_unsubscribe_detaches():
    b = StateBroadcaster()
    received: list[dict] = []
    unsubscribe = b.subscribe(received.append)
    b.set(AgentState.THINKING)
    unsubscribe()
    b.set(AgentState.IDLE)
    assert len(received) == 1
    assert b.subscriber_count() == 0
    unsubscribe()  # double-unsubscribe is harmless


def test_bad_subscriber_does_not_break_others():
    b = StateBroadcaster()
    received: list[dict] = []

    def bad(_event):
        raise RuntimeError("boom")

    b.subscribe(bad)
    b.subscribe(received.append)
    event = b.set(AgentState.SPEAKING)
    assert received == [event]
