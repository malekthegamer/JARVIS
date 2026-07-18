"""Slice 26 — the undo stack (spec §1.4's last clause: "undo where
technically possible").

A small, bounded, in-memory LIFO of reversible actions. Capture sites (the
system-setting wrappers, remember, delete_file) push an entry AFTER their
action succeeds; the `undo_last_action` meta-tool pops the newest and runs
its `undo_fn`.

Deliberate properties:
- **Bounded (5).** Undo is "walk back what just happened", not history —
  a deep stack invites restoring stale state over newer, wanted state.
- **Process-scoped.** Cleared on restart, like the chain tracker. An undo
  entry holds a live closure (a pre-change value, a quarantine token) that
  has no meaning in a later process; persisting it would fake a guarantee.
- **Pop-on-attempt.** A failed undo_fn does NOT stay on the stack: a
  permanently-failing entry (e.g. a purged quarantine) would jam every
  older entry beneath it forever. The failure is reported honestly instead.
- **undo_fns call MECHANISM-level functions** (system.set_volume,
  files.restore_file, MemoryStore.delete_by_id) — never the registry
  wrappers — so an undo can never recursively push undo entries.

Only genuinely reversible verbs may push here. media_key / close_tabs /
send_email / run_shell must never appear (test-pinned): a fake undo is
worse than none.

Thread-safety: one RLock, same posture as MemoryStore. In practice pushes
and pops are already serialized by the server _busy guard + brain RLock.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

# Newest 5 reversible actions — older entries are displaced silently.
UNDO_STACK_MAX = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class UndoEntry:
    tool: str                          # the tool that ran (e.g. "set_volume")
    description: str                   # human phrase: "volume change (was 40%)"
    undo_fn: Callable[[], dict]        # -> {"ok": bool, "message": str}
    ts: str = field(default_factory=_now)
    chain_id: str | None = None


class UndoStack:
    def __init__(self, maxlen: int = UNDO_STACK_MAX) -> None:
        self._lock = threading.RLock()
        self._entries: deque[UndoEntry] = deque(maxlen=maxlen)

    def push(self, entry: UndoEntry) -> None:
        """Record a completed reversible action. Stamps the current chain_id
        (if a chain is running) so the audit trail can group undo with the
        action it reverses. Never raises — losing an undo entry must not
        fail the action that succeeded."""
        try:
            if entry.chain_id is None:
                from jarvis.core import chain  # lazy — avoids import cycles
                tracker = chain.current()
                entry.chain_id = tracker.chain_id if tracker else None
        except Exception:
            pass
        with self._lock:
            self._entries.append(entry)

    def pop(self) -> UndoEntry | None:
        with self._lock:
            return self._entries.pop() if self._entries else None

    def peek(self) -> UndoEntry | None:
        with self._lock:
            return self._entries[-1] if self._entries else None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# One process-wide stack (like memory_store / confirmations).
undo_stack = UndoStack()
