"""Background scheduler for recurring workflows and reminder checks.

Runs `schedule` jobs on a daemon thread. Results surface as dashboard
notifications ONLY (core.notifications) — never spoken unprompted.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import schedule

_started = False
_lock = threading.Lock()


def start() -> None:
    """Idempotently start the scheduler thread."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    thread = threading.Thread(target=_run, name="jarvis-scheduler", daemon=True)
    thread.start()


def _run() -> None:
    while True:
        try:
            schedule.run_pending()
        except Exception:
            pass  # a failing job never kills the scheduler
        time.sleep(1)


def every_minutes(minutes: int, job: Callable, tag: str = "") -> None:
    j = schedule.every(minutes).minutes.do(_safe(job))
    if tag:
        j.tag(tag)


def daily_at(hhmm: str, job: Callable, tag: str = "") -> None:
    j = schedule.every().day.at(hhmm).do(_safe(job))
    if tag:
        j.tag(tag)


def clear(tag: str) -> None:
    schedule.clear(tag)


def _safe(job: Callable) -> Callable:
    def wrapper():
        try:
            job()
        except Exception:
            pass
    return wrapper
