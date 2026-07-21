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
        # "auto" = ElevenLabs if configured, else `preferred`, else edge, else pyttsx3.
        "active": "auto",
        "preferred": "edge_tts",
        "edge_voice": "en-GB-RyanNeural",
        "elevenlabs_voice_id": "",    # slice 23: ElevenLabs picker ("" = Rachel default)
        "pyttsx3_rate": 180,
    },
    "stt": {
        "active": "google",           # free, keyless, proven default
        "mic_device_index": None,     # None = auto-detect real mic (see jarvis.voice.capture)
        "mic_device_name": "",        # pinned by NAME too — survives index shifts
        "whisper_model": "medium",    # slice 23: local-Whisper size (first use downloads it)
        "whisper_device": "cuda",     # cuda (float16 on Blackwell) -> auto CPU int8 fallback
    },
    "confirm": {
        "timeout_s": 30,  # no answer within this window -> action cancelled
    },
    "vision": {
        # Vision FALLBACK for click targeting — only runs when the fast text
        # path can't identify an element (see jarvis.primitives.vision).
        "enabled": True,          # kill switch; off -> fast-path failure fails as before
        "max_edge_px": 1024,      # downscale the window crop's longest edge to this
        "min_confidence": 0.5,    # below this, fail closed to CONFIRM (never click blind)
        # Slice 17 — pre-click point verification. Vision can label a control
        # correctly but POINT at its neighbour (measured slice 16), so the CONFIRM
        # modal could name what you approved while the click landed one icon over.
        # Before a vision click fires, re-check what is ACTUALLY at the point:
        # free via UIA name where one exists, else a grounded crop re-read.
        # Mismatch -> refuse, never click. Vision path only; fast text path untouched.
        "verify_click_point": True,
        "verify_pad_px": 46,      # crop half-size; enough context to ID a glyph,
                                  # still centred on ONE control (measured: 34 was
                                  # too tight — isolated crude icons got misread)
        "verify_upscale": 6,      # zoom the crop so a 40px icon is legible
    },
    "telemetry": {
        # HUD system readouts (spec §2.3). Sampled server-side ONLY while a
        # HUD is connected; GPU (nvidia-smi subprocess) every 3rd tick.
        "enabled": True,        # kill switch
        "interval_s": 2.0,      # tick period
    },
    "shell": {
        # run_shell (spec §1.2) — highest-risk verb. Every call is
        # CONFIRM-gated on the verbatim command; a narrow denylist refuses
        # catastrophic shapes outright.
        "enabled": True,        # kill switch; off -> withheld from the model
        "timeout_s": 30,        # execution wall-clock before the tree is killed
    },
    "email": {
        # send_email (spec §1.6 script #3) — the first outward-reaching verb.
        # Every send is CONFIRM-gated on the verbatim message; attachments
        # are caged to data/agent_files/. Off -> withheld from the model.
        "enabled": True,
    },
    "files": {
        # Caged file authoring (slice 30): write_file/read_file, scoped to
        # data/agent_files/ by the same _contained() cage as delete_file.
        # Size caps only — no kill-switch (parity with delete/search, which
        # have none; the cage + CONFIRM-on-overwrite + undo are the boundary).
        "max_write_kb": 256,
        "max_read_kb": 256,
    },
    "fs": {
        # Real-filesystem access (slices 32-33): list_directory / delete_path
        # (Recycle Bin) / create_shortcut + write/read/move/rename/copy_path,
        # ANYWHERE on the PC. The most powerful surface — the kill-switch
        # withholds ALL of it when off. Safety: CONFIRM on the verbatim path +
        # a catastrophic-path denylist backstop + Recycle-Bin (recoverable)
        # deletes and overwrites.
        "enabled": True,
        "max_write_kb": 256,        # cap write_path content
    },
    "memory": {
        # Long-term cross-session memory (spec §1.5). Explicit-intent writes
        # only; DPAPI-encrypted at rest; relevance-gated retrieval.
        # Slice 19: semantic retrieval via a LOCAL embedding model
        # (python -m jarvis.core.embedder --setup); no model -> lexical.
        "enabled": True,
        "retrieve_k": 5,             # max memories injected per message
        "relevance_threshold": 1,    # min shared content-tokens (lexical/guard)
        "semantic_threshold": 0.35,  # min cosine to surface — tuned on the
                                     # golden set (0.32-0.35 plateau: recall
                                     # 0.818, false-surface == lexical baseline)
                                     # Slice 34 re-measured: do NOT lower this
                                     # to chase the 4 remaining misses. They
                                     # score 0.169-0.280 while 3 UNRELATED
                                     # negatives score 0.292-0.453, so every
                                     # lower value costs more privacy than it
                                     # buys recall (0.30 buys zero recall and
                                     # doubles false-surface). See
                                     # harness_memory_eval.py --verbose.
        "pinned_max": 10,            # newest pinned prefs always in the prompt
    },
    "wake": {
        # Wake word (slice 13) — an ALTERNATIVE trigger to push-to-talk, via
        # openWakeWord ("hey jarvis"). Opt-in (off by default); when on, an
        # always-on local listener starts with the server. Audio is processed
        # locally and discarded pre-trigger (see jarvis.voice.wake).
        "enabled": False,
        "model": "hey_jarvis",       # pretrained openWakeWord model name
        "threshold": 0.5,            # Stage-0: silence 0.000, speech 0.78–0.998
        "follow_up_timeout_s": 5,    # how long to wait for the command after "hey jarvis"
        "cooldown_s": 2.0,           # collapse a burst of frames into one wake
    },
    "web": {
        # Browser automation (slice 14) — a DEDICATED, isolated Playwright
        # browser (fresh profile, NO user logins). Per-action gating: a
        # cross-origin navigation and a committal in-page click are
        # CONFIRM-gated; page content is treated as untrusted data. Off ->
        # the verbs are withheld from the model (like shell/email).
        "enabled": True,
        "headless": False,      # visible by default so the user watches; tests force True
        "timeout_s": 15,        # per-action wall-clock before an honest failure
        "max_read_chars": 5000, # cap on returned page text
        # Slice 24 — real-browser mode. "isolated" (default): fresh Chromium, no
        # logins (the safe sandbox). "real": a DEDICATED real Chrome (its own
        # profile dir) driven via CDP, logged into the user's accounts after a
        # one-time sign-in per site; NAVIGATE + READ only — committal actions
        # (click/fill) are withheld in this mode. Coexists with the user's
        # everyday Chrome (never closes it).
        "profile_mode": "isolated",
        "cdp_port": 9222,
        # Slice 25 — let JARVIS ACT (click/type/submit) in real mode. Default
        # OFF: real mode stays navigate+read until this is explicitly enabled.
        # When on, committal actions (post/buy/send/delete/submit) still CONFIRM;
        # benign clicks/typing run freely.
        "allow_actions": False,
    },
    "search": {
        # Web search (slice 15) — keyless DuckDuckGo (ddgs). A pure read (AUTO);
        # results are wrapped in the SAME untrusted-data boundary as page reads.
        # Off -> the verb is withheld from the model.
        "enabled": True,
        "max_results": 5,
    },
    "input": {
        # Slice 22: cosmetic eased cursor glide before clicks. Same landing
        # pixel, same click semantics; hard time cap keeps it out of the
        # latency budget (~4% of a post-slice-21 chain, worst case).
        "smooth_cursor": True,
        "cursor_move_max_ms": 200,
        # Slice 29: hostile-input bound on scroll() — one wheel notch ≈ one
        # wheel click; a request above this is clamped and the clamp is reported.
        "scroll_max_notches": 20,
    },
    "apps": {
        # Slice 22: launcher-mediated game launches (steam:// / Epic URIs)
        # have no pid to watch and boot slowly — poll for the game's window
        # by name up to this long, then report the dispatch honestly.
        "game_window_wait_s": 20,
    },
    "audit": {
        # Persistent audit log (slice 18) — every execute() call + memory
        # mutation, durable JSONL at data/audit/ (plaintext envelope +
        # DPAPI-encrypted args/result). Rotated files are never auto-deleted.
        "enabled": True,
        "max_file_mb": 5,           # rotate audit.jsonl aside at this size
        "result_max_chars": 2000,   # payload result cap (args never truncated)
    },
    "autostart": False,             # slice 23: HKCU Run key -> tray at login
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
