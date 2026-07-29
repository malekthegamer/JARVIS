"""Slice 41 — the request/response bridge to the JARVIS browser extension.

WHY A BRIDGE. Primitives run on threadpool worker threads and are synchronous;
the extension is on the far end of an asyncio WebSocket. So a worker thread has
to hand a command to the event loop and block until the reply arrives. That is
the same shape `core/confirmations.py` already proves (block on a
threading.Event; the async side sets it), and the Stage-0 probe measured a
round trip at 0.00s from a worker thread with no deadlock before this was
designed.

FAIL CLOSED, ALWAYS. Every wait is bounded, a disconnect releases every waiter
with an error rather than hanging, and "no extension connected" is an explicit
exception — never a silent success. A browser automation call that hangs
forever would wedge the whole agent, since the chain loop is synchronous.
"""
from __future__ import annotations

import asyncio
import itertools
import threading

DEFAULT_TIMEOUT_S = 20.0


class ExtensionUnavailable(RuntimeError):
    """No usable extension connection. Callers translate this into an honest
    user-facing message; it must never be swallowed into a fake success."""


class ExtensionBridge:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ws = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: dict[str, dict] = {}
        self._ids = itertools.count(1)

    # ---------- connection lifecycle (called from the event loop) ----------
    def attach(self, ws, loop) -> None:
        """A new extension socket. If one was already attached the NEWEST wins
        — a stale socket must never keep answering for the user's browser."""
        with self._lock:
            old = self._ws
            self._ws, self._loop = ws, loop
        if old is not None and old is not ws:
            self._fail_all("replaced by a newer extension connection")

    def detach(self, ws) -> None:
        with self._lock:
            if self._ws is not ws:
                return                      # a stale socket closing; ignore
            self._ws = None
            self._loop = None
        self._fail_all("the browser extension disconnected")

    def connected(self) -> bool:
        with self._lock:
            return self._ws is not None

    def deliver(self, msg: dict) -> None:
        """A frame from the extension: wake whoever is waiting on its id."""
        rid = str((msg or {}).get("id") or "")
        with self._lock:
            entry = self._pending.pop(rid, None)
        if entry is not None:
            entry["reply"] = msg
            entry["event"].set()

    def _fail_all(self, reason: str) -> None:
        with self._lock:
            pending, self._pending = self._pending, {}
        for entry in pending.values():
            entry["error"] = reason
            entry["event"].set()

    async def heartbeat(self) -> bool:
        """Put a frame on the wire to keep Chrome from killing the extension.

        MEASURED, and the reason bugs 2+3 existed: with no traffic Chrome
        terminates the idle MV3 service worker after ~30s, and the extension's
        reconnect alarm only fires once a minute — so the browser was
        unreachable roughly HALF the time. browse_navigate then failed and the
        model fell back to opening a new window or typing the URL by hand.

        WebSocket traffic resets that idle timer (Stage 0: 20s pings held the
        worker alive for 100s straight), so this is the whole fix. Fire and
        forget — we do not wait for the pong; the traffic itself is the point.
        Returns False when nothing is connected."""
        with self._lock:
            ws = self._ws
        if ws is None:
            return False
        try:
            await ws.send_json({"cmd": "ping", "id": "heartbeat"})
            return True
        except Exception:
            return False

    # ---------- the blocking call (called from a WORKER THREAD) ----------
    def send(self, command: str, payload: dict | None = None,
             timeout: float = DEFAULT_TIMEOUT_S) -> dict:
        """Send a command to the extension and block until it replies.

        Raises ExtensionUnavailable when there is no extension, when it drops
        mid-request, or on timeout. Never returns a partial or invented result.
        """
        with self._lock:
            ws, loop = self._ws, self._loop
            if ws is None or loop is None:
                raise ExtensionUnavailable(
                    "Your browser isn't connected to JARVIS. Check the JARVIS "
                    "extension is enabled in Chrome — after a JARVIS restart it "
                    "can take up to a minute to reconnect.")
            rid = f"r{next(self._ids)}"
            entry: dict = {"event": threading.Event(), "reply": None,
                           "error": None}
            self._pending[rid] = entry

        frame = {"cmd": command, "id": rid}
        if payload:
            frame.update(payload)
        try:
            asyncio.run_coroutine_threadsafe(ws.send_json(frame), loop)
        except Exception as exc:                       # loop closed mid-send
            with self._lock:
                self._pending.pop(rid, None)
            raise ExtensionUnavailable(
                f"Couldn't reach the browser extension: {exc}") from exc

        if not entry["event"].wait(timeout):
            with self._lock:
                self._pending.pop(rid, None)
            raise ExtensionUnavailable(
                f"The browser extension didn't answer within {timeout:.0f}s.")
        if entry["error"]:
            raise ExtensionUnavailable(entry["error"])
        return entry["reply"] or {}


bridge = ExtensionBridge()
