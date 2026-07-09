"""The spec §2.4 state machine — THE seam between the agent and every UI.

States: IDLE, LISTENING, THINKING, EXECUTING, SPEAKING. One process-wide
broadcaster; the server subscribes and forwards events over WebSocket,
tests subscribe directly.

Events carry a monotonic `seq` so consumers can detect reordering. Delivery
happens under the lock — subscribers MUST be fast and non-blocking (the
server enqueues to asyncio; it never does network I/O inline).
"""
from __future__ import annotations

import threading
from enum import Enum
from typing import Callable


class AgentState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    CONFIRMING = "confirming"  # waiting on the user's yes/no; detail = primitive
    EXECUTING = "executing"  # a primitive is running; detail = its name
    SPEAKING = "speaking"


class StateBroadcaster:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: list[Callable[[dict], None]] = []
        self._seq = 0
        self.current: AgentState = AgentState.IDLE

    def set(self, state: AgentState, detail: str = "") -> dict:
        """Enter a state and notify every subscriber, in seq order."""
        with self._lock:
            self.current = state
            return self.emit({"type": "state", "state": state.value, "detail": detail})

    def emit(self, event: dict) -> dict:
        """Broadcast an arbitrary event (plan/step/chain_end, …) through the
        SAME lock and seq stream as state events, so every consumer sees one
        totally-ordered feed. Does not touch the state machine."""
        with self._lock:
            self._seq += 1
            event = {**event, "seq": self._seq}
            for fn in list(self._subscribers):
                try:
                    fn(event)
                except Exception:
                    pass  # a bad subscriber never breaks the state machine
            return event

    def subscribe(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        """Register a listener; returns an unsubscribe callable."""
        with self._lock:
            self._subscribers.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._subscribers:
                    self._subscribers.remove(fn)

        return unsubscribe

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


broadcaster = StateBroadcaster()
