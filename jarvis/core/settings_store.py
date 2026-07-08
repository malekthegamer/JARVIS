"""data/settings.json read/write with hot-reload notification.

save() -> every registered listener (brain, voice_manager, ...) re-resolves
its active provider. No restart needed. Secrets are NOT stored here — they
live in .env (see jarvis.config.set_api_key).
"""
from __future__ import annotations

import copy
import json
import threading
from typing import Any, Callable

from jarvis import config

DEFAULT_SETTINGS: dict[str, Any] = {
    "brain": {
        "active": "gemini",
        "models": {
            # Verified live 2026-07-08: returns "pong". Swap here if it 404s.
            "gemini": "gemini-3.1-flash-lite",
        },
    },
    "tts": {
        # "auto" = use `preferred` if configured, else edge_tts, else pyttsx3.
        "active": "auto",
        "preferred": "edge_tts",
        "edge_voice": "en-GB-RyanNeural",
        "pyttsx3_rate": 180,
    },
    "stt": {
        "active": "google",           # free, keyless, proven default
        "mic_device_index": None,     # None = auto-detect real mic (see jarvis.voice.capture)
        "mic_device_name": "",        # pinned by NAME too — survives index shifts
    },
    "confirm": {
        "timeout_s": 30,  # no answer within this window -> action cancelled
    },
    "history_max_messages": 40,
}


class SettingsStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._listeners: list[Callable[[], None]] = []
        self._data: dict[str, Any] = {}
        self.load()

    # ---------- persistence ----------
    def load(self) -> None:
        with self._lock:
            data = copy.deepcopy(DEFAULT_SETTINGS)
            if config.SETTINGS_FILE.exists():
                try:
                    on_disk = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
                    _deep_merge(data, on_disk)
                except (json.JSONDecodeError, OSError):
                    pass  # corrupt settings never crash the app; defaults win
            self._data = data
            if not config.SETTINGS_FILE.exists():
                self._write()

    def _write(self) -> None:
        config.SETTINGS_FILE.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def save(self, new_data: dict[str, Any] | None = None) -> None:
        """Persist (optionally merging new values) and fire reload listeners."""
        with self._lock:
            if new_data:
                _deep_merge(self._data, new_data)
            self._write()
        self.reload()

    def reload(self) -> None:
        """Hot-swap hook: notify every listener that settings changed."""
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                pass  # a bad listener never breaks the store

    def on_reload(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    # ---------- access ----------
    def get(self, path: str, default: Any = None) -> Any:
        """Dotted-path getter, e.g. get('brain.active')."""
        with self._lock:
            node: Any = self._data
            for part in path.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, path: str, value: Any, persist: bool = True) -> None:
        with self._lock:
            node = self._data
            parts = path.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        if persist:
            self.save()

    def all(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)


def _deep_merge(base: dict, extra: dict) -> None:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


settings = SettingsStore()
