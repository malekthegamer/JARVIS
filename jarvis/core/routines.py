"""Routines (slice 48) — a named, saved chain the user replays by name.

    "work mode" -> open VS Code, mute Spotify, DND on

WHAT THIS IS NOT: authority. A routine is a stored LIST OF STEPS, nothing more.
It confers no permission on those steps — every one of them is re-gated at RUN
time by primitives.execute() (kill switch -> tier -> CONFIRM -> audit), exactly
as if the model had asked for it fresh. That is why a prompt-injected
save_routine cannot smuggle an action past the gate: it can only store an
intention that still has to survive the same checks later. Replaying via
execute() makes re-confirmation the FREE, default behaviour — skipping it would
take deliberate extra work, which is the property you want.

Stored DPAPI-encrypted: routine steps carry app names, URLs and file paths,
which is personal data. The write path REFUSES rather than fall back to
plaintext, mirroring MemoryStore._persist. Every read path degrades honestly —
a missing / corrupt / foreign-encrypted store loads as empty and never crashes.

MEASURED (Stage 0, scratchpad/probe_routines.py): with saved names injected into
the prompt, the model composed correct steps 4/4 and mapped a BARE "work mode"
to run_routine 4/4, self-normalising "Work Mode" and "work-mode" to the stored
name. WITHOUT that prompt block it was 0/4 (it called list_routines instead) —
so the name-matching here is a backstop for hand-edited files and stale prompts,
not the primary mechanism.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from jarvis import config
from jarvis.core import dpapi

DEFAULT_PATH = config.DATA_DIR / "routines.bin"

# Bounds. A routine is a convenience, not a program: these keep a runaway model
# (or a hostile page) from storing something unbounded.
MAX_STEPS = 40
MAX_ROUTINES = 100
MAX_NAME_LEN = 80

# A routine may never contain these. run_routine is the recursion case: a
# routine that runs a routine is an unbounded loop, refused at the door rather
# than defended with a depth counter at run time.
FORBIDDEN_TOOLS = frozenset({"run_routine"})


def normalize(name: str) -> str:
    """Canonical key for a routine name: casefolded, whitespace-collapsed."""
    return " ".join(str(name or "").split()).casefold()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def valid_steps(steps) -> tuple[bool, str]:
    """(ok, why_not) for a step list.

    Deliberately a module function, not a method: save() validates, and
    _run_run_routine RE-validates at run time. The file on disk can be edited by
    hand, so the run-time check is the real boundary — the save-time one is only
    the fast, friendly failure.
    """
    from jarvis.primitives import PRIMITIVES   # lazy: avoids a circular import

    if not isinstance(steps, list) or not steps:
        return False, "a routine needs at least one step"
    if len(steps) > MAX_STEPS:
        return False, f"too many steps (max {MAX_STEPS})"
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            return False, f"step {i} is not a step object"
        tool = step.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            return False, f"step {i} has no tool name"
        if tool in FORBIDDEN_TOOLS:
            return False, (f"step {i} would run another routine — routines "
                           f"cannot contain other routines")
        if tool not in PRIMITIVES:
            return False, f"step {i} uses an unknown tool '{tool}'"
        args = step.get("args", {})
        if args is not None and not isinstance(args, dict):
            return False, f"step {i} has malformed arguments"
    return True, ""


class RoutineStore:
    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._records: list[dict] = self._load()

    # ---------- persistence (honest degradation) ----------
    def _load(self) -> list[dict]:
        try:
            if not self.path.exists():
                return []
            data = json.loads(dpapi.unprotect(self.path.read_bytes()).decode("utf-8"))
            if not isinstance(data, list):
                return []
            return [r for r in data
                    if isinstance(r, dict) and r.get("name") and r.get("steps")]
        except Exception:
            # missing / corrupt / foreign-encrypted / not-JSON -> start empty
            return []

    def _persist(self) -> None:
        if not dpapi.available():
            raise RuntimeError("secure storage is unavailable — refusing to "
                               "write your routines in plaintext")
        blob = dpapi.protect(json.dumps(self._records).encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(blob)

    # ---------- reads ----------
    def get(self, name: str) -> dict | None:
        key = normalize(name)
        with self._lock:
            for r in self._records:
                if normalize(r["name"]) == key:
                    return dict(r)
        return None

    def exists(self, name: str) -> bool:
        return self.get(name) is not None

    def all(self) -> list[dict]:
        with self._lock:
            return sorted((dict(r) for r in self._records),
                          key=lambda r: normalize(r["name"]))

    def names(self) -> list[str]:
        return [r["name"] for r in self.all()]

    # ---------- writes ----------
    def save(self, name: str, steps) -> dict:
        """Create or replace. Raises ValueError on anything invalid, RuntimeError
        if it cannot be stored securely."""
        clean = " ".join(str(name or "").split())
        if not clean:
            return self._bad("a routine needs a name")
        if len(clean) > MAX_NAME_LEN:
            return self._bad(f"that name is too long (max {MAX_NAME_LEN})")
        ok, why = valid_steps(steps)
        if not ok:
            return self._bad(why)

        with self._lock:
            key = normalize(clean)
            existing = next((r for r in self._records
                             if normalize(r["name"]) == key), None)
            if existing is None and len(self._records) >= MAX_ROUTINES:
                return self._bad(f"you already have {MAX_ROUTINES} routines "
                                 f"(the maximum) — delete one first")
            record = {"name": clean, "steps": steps,
                      "saved": _now() if existing is None else existing.get("saved", _now()),
                      "updated": _now()}
            if existing is not None:
                self._records[self._records.index(existing)] = record
            else:
                self._records.append(record)
            self._persist()
            return dict(record)

    def delete(self, name: str) -> bool:
        key = normalize(name)
        with self._lock:
            before = len(self._records)
            self._records = [r for r in self._records
                             if normalize(r["name"]) != key]
            if len(self._records) == before:
                return False
            self._persist()
            return True

    @staticmethod
    def _bad(why: str):
        raise ValueError(why)


routine_store = RoutineStore()
