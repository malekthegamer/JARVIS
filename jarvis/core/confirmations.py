"""The CONFIRM-tier gate (spec §1.4) — trust-critical, FAIL CLOSED.

One rule everywhere: no abnormal path may ever read as approval. Timeout,
decline, supersession, broken UI plumbing, late/duplicate/unknown resolve —
every one of them denies. Approval requires an explicit resolve(id, True)
arriving while the request is still pending. (Shape salvaged from
legacy/core/confirmations.py, which failed closed on frontend exceptions.)

Synchronous-with-timeout by design: request() blocks its (threadpool) thread
on a threading.Event; the asyncio loop and HUD stay responsive, and the
server's WS receive loop delivers resolve() calls.

Single-flight: at most one confirmation is ever pending. A second request()
while one is pending returns superseded immediately — never queued, never a
stacked modal.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Callable

DEFAULT_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class Decision:
    approved: bool
    reason: str  # approved | declined | timeout | superseded | error


class ConfirmationManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: list[Callable[[dict], None]] = []
        self._pending: dict | None = None

    # ---------- events (server forwards these to the HUD) ----------
    def subscribe(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._subscribers:
                    self._subscribers.remove(fn)

        return unsubscribe

    def _emit(self, event: dict) -> None:
        """Raises if any subscriber fails — request() treats that as deny."""
        for fn in list(self._subscribers):
            fn(event)

    def _emit_safe(self, event: dict) -> None:
        """Resolution notices are informational — never let them raise."""
        for fn in list(self._subscribers):
            try:
                fn(event)
            except Exception:
                pass

    def pending_event(self) -> dict | None:
        """The confirm_request to replay to a (re)connecting HUD, or None."""
        with self._lock:
            if self._pending is None:
                return None
            p = self._pending
            return {"type": "confirm_request", "id": p["id"],
                    "description": p["description"], "timeout_s": p["timeout_s"]}

    # ---------- the gate ----------
    def request(self, description: str,
                timeout_s: float = DEFAULT_TIMEOUT_S) -> Decision:
        """Block until the user answers or the timeout elapses. Fail closed."""
        with self._lock:
            if self._pending is not None:
                return Decision(False, "superseded")
            entry = {
                "id": uuid.uuid4().hex,
                "description": description,
                "timeout_s": timeout_s,
                "event": threading.Event(),
                "approved": False,
            }
            self._pending = entry

        try:
            self._emit({"type": "confirm_request", "id": entry["id"],
                        "description": description, "timeout_s": timeout_s})
        except Exception:
            # Couldn't reliably show the prompt -> the user never saw it -> deny.
            with self._lock:
                self._pending = None
            self._emit_safe({"type": "confirm_resolved", "id": entry["id"],
                             "result": "error"})
            return Decision(False, "error")

        answered = entry["event"].wait(timeout_s)
        with self._lock:
            # `answered` (captured from wait) is authoritative: a resolve that
            # races in just after the timeout still reads as timeout -> deny.
            approved = answered and bool(entry["approved"])
            self._pending = None
        reason = "approved" if approved else ("declined" if answered else "timeout")
        self._emit_safe({"type": "confirm_resolved", "id": entry["id"],
                         "result": reason})
        return Decision(approved, reason)

    def resolve(self, confirm_id: str, approved: bool) -> bool:
        """Deliver the user's answer. Returns False (and does nothing) for
        unknown ids, already-answered requests, or requests that ended."""
        with self._lock:
            entry = self._pending
            if entry is None or entry["id"] != confirm_id or entry["event"].is_set():
                return False
            entry["approved"] = bool(approved)
            entry["event"].set()
            return True


confirmations = ConfirmationManager()
