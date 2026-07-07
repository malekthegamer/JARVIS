"""Silent visual notifications — dashboard only, per the reactive-only rule.

Background results (system thresholds, price targets, due reminders) land here.
They are NEVER spoken aloud unless the user asks about them.
"""
from __future__ import annotations

import itertools
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Callable

_lock = threading.Lock()
_queue: deque[dict] = deque(maxlen=200)
_ids = itertools.count(1)
_subscribers: list[Callable[[dict], None]] = []


def notify(title: str, message: str, source: str = "system") -> dict:
    item = {
        "id": next(_ids),
        "ts": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "message": message,
        "source": source,
        "read": False,
    }
    with _lock:
        _queue.append(item)
    for fn in list(_subscribers):
        try:
            fn(item)
        except Exception:
            pass
    return item


def all_notifications() -> list[dict]:
    with _lock:
        return list(_queue)


def unread() -> list[dict]:
    with _lock:
        return [n for n in _queue if not n["read"]]


def mark_read(notification_id: int | None = None) -> None:
    """Mark one (or all, if id is None) notifications as read."""
    with _lock:
        for n in _queue:
            if notification_id is None or n["id"] == notification_id:
                n["read"] = True


def subscribe(fn: Callable[[dict], None]) -> None:
    """Dashboard hook for live WebSocket push."""
    _subscribers.append(fn)
