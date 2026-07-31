"""Scheduled routines (slice 50) — JARVIS acting without being asked.

A schedule is *a saved routine + a time*. It deliberately cannot point at a
free-text instruction: an unattended agent choosing its own actions while nobody
watches is a different trust decision, and the owner scoped this to routines
only.

THE CARDINAL RULE lives in primitives._execute_inner, not here: a scheduled run
sets `tracker.unattended`, and any step resolving to a non-AUTO tier is PARKED —
not executed, and not prompted. Prompting an empty room is not a gate; it is a
30-second timeout and a modal nobody sees. **An unattended agent must never be
able to approve itself.**

MEASURED (Stage 0, scratchpad/probe_schedule_tiers.py): realistic scheduled
routines resolve 4/4 AUTO through the real classifiers — morning (launch_app,
set_volume, set_dnd, set_brightness), evening (set_dnd, media_key, set_volume,
set_brightness), digest (web_search, screen_query, read_ui_tree, get_volume) —
while a hostile routine parks 3 of 4. So AUTO-only bounds the feature without
gutting it.

Time is deliberately LOCAL and naive: "8am" means 8am on this machine's clock,
which is what a person means. The honest edge is DST — on the two days a year
the clock shifts, a job may run an hour early or late relative to solar time.
Not solved, stated.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jarvis import config
from jarvis.core import dpapi

DEFAULT_PATH = config.DATA_DIR / "schedules.bin"

KINDS = ("daily", "weekdays", "weekly")
MAX_SCHEDULES = 50
# How late is still "due" rather than "missed". The PC being asleep at 8am must
# not make the morning routine fire at 6pm.
DEFAULT_GRACE_MINUTES = 60


def routine_exists(name: str) -> bool:
    """Indirection so tests can stub it without a routine store on disk."""
    try:
        from jarvis.core.routines import routine_store
        return routine_store.get(name) is not None
    except Exception:
        return False


def _parse_at(at: str) -> tuple[int, int]:
    """'08:00' -> (8, 0). Raises ValueError on anything else."""
    text = str(at or "").strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError(f"time must look like 08:00, got {at!r}")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"time must look like 08:00, got {at!r}") from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"time out of range: {at!r}")
    return hour, minute


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ScheduleStore:
    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._records: list[dict] = self._load()

    # ---------- persistence (mirrors RoutineStore exactly) ----------
    def _load(self) -> list[dict]:
        try:
            if not self.path.exists():
                return []
            data = json.loads(dpapi.unprotect(self.path.read_bytes()).decode("utf-8"))
            if not isinstance(data, list):
                return []
            return [r for r in data
                    if isinstance(r, dict) and r.get("routine") and r.get("at")]
        except Exception:
            return []   # missing / corrupt / foreign-encrypted -> start empty

    def _persist(self) -> None:
        if not dpapi.available():
            raise RuntimeError("secure storage is unavailable — refusing to "
                               "write your schedules in plaintext")
        blob = dpapi.protect(json.dumps(self._records).encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(blob)

    # ---------- writes ----------
    def add(self, routine: str, *, kind: str = "daily", at: str = "08:00",
            weekday: int | None = None, announce: bool = False) -> dict:
        name = " ".join(str(routine or "").split())
        if not name:
            raise ValueError("a schedule needs a routine name")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        hour, minute = _parse_at(at)
        if not routine_exists(name):
            raise ValueError(f"there is no routine called {name!r} — save it first")
        if kind == "weekly" and weekday is None:
            weekday = 0
        with self._lock:
            if len(self._records) >= MAX_SCHEDULES:
                raise ValueError(f"you already have {MAX_SCHEDULES} schedules "
                                 f"(the maximum) — cancel one first")
            record = {
                "id": uuid.uuid4().hex[:8],
                "routine": name,
                "kind": kind,
                "at": f"{hour:02d}:{minute:02d}",
                "weekday": weekday,
                "announce": bool(announce),
                "created": _now(),
                "last_run": None,      # ISO local datetime of the last firing
            }
            self._records.append(record)
            self._persist()
            return dict(record)

    def cancel(self, schedule_id: str) -> bool:
        with self._lock:
            before = len(self._records)
            self._records = [r for r in self._records
                             if r.get("id") != str(schedule_id)]
            if len(self._records) == before:
                return False
            self._persist()
            return True

    def mark_ran(self, routine: str, when: datetime) -> None:
        """Stamp BEFORE executing (risk 3): a crash mid-run must not leave the
        job looking un-run, or the next tick fires it again."""
        with self._lock:
            for r in self._records:
                if r["routine"] == routine:
                    r["last_run"] = when.isoformat(timespec="seconds")
            try:
                self._persist()
            except Exception:
                pass   # a stamp failure must not stop the run being attempted

    def mark_ran_id(self, schedule_id: str, when: datetime) -> None:
        with self._lock:
            for r in self._records:
                if r.get("id") == schedule_id:
                    r["last_run"] = when.isoformat(timespec="seconds")
            try:
                self._persist()
            except Exception:
                pass

    # ---------- reads ----------
    def all(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._records]

    def due(self, now: datetime,
            grace_minutes: int = DEFAULT_GRACE_MINUTES) -> list[dict]:
        """Schedules that should fire at `now` — local, naive datetimes.

        Due means ALL of: the day matches the kind, `now` is at/after the
        scheduled time, it is no more than `grace_minutes` late, and it has not
        already run in this window.
        """
        out = []
        with self._lock:
            for r in self._records:
                try:
                    if self._is_due(r, now, grace_minutes):
                        out.append(dict(r))
                except Exception:
                    continue   # one malformed record must not break the tick
        return out

    @staticmethod
    def _is_due(record: dict, now: datetime, grace_minutes: int) -> bool:
        hour, minute = _parse_at(record["at"])
        kind = record.get("kind", "daily")

        if kind == "weekdays" and now.weekday() >= 5:
            return False
        if kind == "weekly" and now.weekday() != int(record.get("weekday") or 0):
            return False

        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < scheduled:
            return False                     # not yet
        if now - scheduled > timedelta(minutes=grace_minutes):
            return False                     # missed, not due (risk 4)

        last = record.get("last_run")
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
            except ValueError:
                return True
            # Already fired for this occurrence? Also covers a clock that jumped
            # backwards: a last_run at or after this window's start blocks it.
            if last_dt >= scheduled:
                return False
        return True


schedule_store = ScheduleStore()
