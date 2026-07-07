"""Long-term memory: durable facts in data/memory.json.

Session memory (the running conversation) lives in brain.py; this module is
only for facts that must survive restarts — names, preferences, routines.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

import config

_lock = threading.RLock()


def _load() -> dict:
    if config.MEMORY_FILE.exists():
        try:
            return json.loads(config.MEMORY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save(data: dict) -> None:
    config.MEMORY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def remember(key: str, value: str) -> str:
    key = key.strip().lower()
    with _lock:
        data = _load()
        data[key] = {"value": value, "saved": datetime.now(timezone.utc).isoformat()}
        _save(data)
    return f"Remembered: {key} = {value}"


def recall(key: str) -> str | None:
    with _lock:
        entry = _load().get(key.strip().lower())
    return entry["value"] if entry else None


def forget(key: str) -> bool:
    key = key.strip().lower()
    with _lock:
        data = _load()
        if key in data:
            del data[key]
            _save(data)
            return True
    return False


def list_memories() -> dict[str, str]:
    with _lock:
        return {k: v["value"] for k, v in _load().items()}


def context_block() -> str:
    """Formatted memories for injection into the brain's system context."""
    memories = list_memories()
    if not memories:
        return ""
    lines = "\n".join(f"- {k}: {v}" for k, v in memories.items())
    return f"\nThings you remember about the user (long-term memory):\n{lines}\n"
