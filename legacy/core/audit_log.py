"""Append-only action log (data/logs/actions.log) + live feed for the dashboard.

Records ACTIONS (skill, params, timestamp) — never conversation content.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

import config

_lock = threading.Lock()
_recent: deque[dict] = deque(maxlen=200)
_subscribers: list[Callable[[dict], None]] = []


def log_action(skill: str, action: str, params: dict[str, Any] | None = None, result: str = "ok") -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "skill": skill,
        "action": action,
        "params": _scrub(params or {}),
        "result": result[:200],
    }
    line = json.dumps(entry, ensure_ascii=False)
    with _lock:
        try:
            with open(config.ACTIONS_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # logging must never crash the app
        _recent.append(entry)
    for fn in list(_subscribers):
        try:
            fn(entry)
        except Exception:
            pass


def recent(n: int = 50) -> list[dict]:
    with _lock:
        return list(_recent)[-n:]


def subscribe(fn: Callable[[dict], None]) -> None:
    """Dashboard hook: called with each new entry for live WebSocket push."""
    _subscribers.append(fn)


def _scrub(params: dict) -> dict:
    """Never let anything key-like into the log."""
    out = {}
    for k, v in params.items():
        if any(s in k.lower() for s in ("key", "token", "secret", "password")):
            out[k] = "•••"
        else:
            out[k] = str(v)[:300] if isinstance(v, str) else v
    return out
