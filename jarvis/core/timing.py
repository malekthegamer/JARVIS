"""Opt-in latency recorder for the VOICE path (slice 57).

WHY THIS EXISTS. The project could measure a tool chain (`harness_latency_eval.py`
wraps model/execute/UIA/vision) but had **no mouth-to-ear number at all** — STT,
wake, and the `_respond` funnel were uninstrumented. So the owner's "it's too
slow" could never be answered with anything but a guess, and any speed work would
have shipped as a claim rather than a measurement.

DESIGN: **off unless `JARVIS_VOICE_TIMING=1`.** This is meant to be switched on in
REAL use on the owner's machine, not only inside a harness — a profiler that only
works in a lab measures the lab. When off, `span()` and `mark()` do nothing and
store nothing, so normal operation pays no cost.

Two shapes, because two different questions:
  * `span(name)`  — how long did this take?      (a duration)
  * `mark(name)`  — when did this happen?        (an instant)

Time-to-first-audio is the gap between two *marks* (speech ended → pygame began
playing), which is why `mark` exists at all.
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager

ENV = "JARVIS_VOICE_TIMING"

_lock = threading.Lock()
_records: list[dict] = []


def enabled() -> bool:
    """Read the env var EVERY call rather than caching at import: a test (or the
    owner) may flip it after this module is first imported."""
    return bool(os.environ.get(ENV))


def reset() -> None:
    with _lock:
        _records.clear()


def records() -> list[dict]:
    """A copy, in the order events happened."""
    with _lock:
        return list(_records)


def mark(name: str) -> None:
    """Record an INSTANT. `ms` is None to distinguish it from a duration."""
    if not enabled():
        return
    with _lock:
        _records.append({"name": name, "t": time.monotonic(), "ms": None})


@contextmanager
def span(name: str):
    """Record a DURATION. Recorded in `finally`, so a raising body is still
    measured — a failing brain round is exactly when the number matters — and so
    the span can never swallow the exception."""
    if not enabled():
        yield
        return
    t0 = time.monotonic()
    try:
        yield
    finally:
        with _lock:
            _records.append({"name": name, "t": t0,
                             "ms": (time.monotonic() - t0) * 1000.0})


def elapsed_ms(start_name: str, end_name: str) -> float | None:
    """Gap between the FIRST `start_name` and the LAST `end_name`, in ms.

    Returns None when either mark is absent. Never invent a number: a missing
    mark means that path did not run, and reporting a plausible-looking 0.0
    would be worse than reporting nothing.
    """
    with _lock:
        starts = [r["t"] for r in _records if r["name"] == start_name]
        ends = [r["t"] for r in _records if r["name"] == end_name]
    if not starts or not ends:
        return None
    return (ends[-1] - starts[0]) * 1000.0


def report() -> str:
    """Human-readable dump. Never raises — it is called from `finally` blocks."""
    try:
        rows = records()
        if not rows:
            return "voice timing: nothing recorded (set JARVIS_VOICE_TIMING=1)"
        t0 = rows[0]["t"]
        out = ["voice timing:"]
        for r in rows:
            at = (r["t"] - t0) * 1000.0
            dur = "        " if r["ms"] is None else f"{r['ms']:8.1f}ms"
            kind = "mark" if r["ms"] is None else "span"
            out.append(f"  +{at:8.1f}ms  {dur}  {kind}  {r['name']}")
        return "\n".join(out)
    except Exception as exc:      # pragma: no cover - defensive
        return f"voice timing: report failed ({exc})"
