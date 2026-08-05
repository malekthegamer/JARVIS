"""Slice 67 — refused is not broken. Fixing the instrument I choose work with.

tests/harness_reliability.py reads the audit log and reports a failure rate.
That number picked the last four slices, and it is wrong in three ways — each of
which has already cost real planning time:

1. **A BLOCKED action counts as a failure.** "shell execution is disabled in
   settings", "real-filesystem access is disabled", "I won't create a folder
   inside C:\\Windows\\System32" — every one of those is the safety system
   working exactly as designed, scored as breakage.
2. **A declined UAC prompt counts as a failure.** Three of 2026-08-05's
   launch_app rows are the owner clicking No on Windows' own consent dialog.
   That is a person exercising a choice, not software breaking.
3. **No date window.** browse_fill's "78% worst verb" and click's "35%" were
   both stale by weeks; planning a slice on each was started and abandoned. The
   slice-62 lesson, repeated.

The chain and the HUD still treat BLOCKED as a failure and that is deliberate —
chain.py:37 says "render as failed, not ok", and a chain that keeps hitting a
denylist SHOULD burn its failure budget. Only the durable record gets the finer
distinction.
"""
from __future__ import annotations

import pytest

from jarvis.core import chain


# ------------------------------------------------- the two status functions

def test_the_chain_still_treats_blocked_as_failed():
    """Unchanged on purpose: the HUD renders it red and it must still consume
    the chain's failure budget and arm the repeat-call breaker."""
    assert chain.status_from_result("BLOCKED: shell execution is disabled") == "failed"
    assert chain.status_from_result("FAILED: nope") == "failed"
    assert chain.status_from_result("CANCELLED: user said no") == "cancelled"
    assert chain.status_from_result("OK: done") == "ok"


def test_the_audit_records_blocked_as_refused():
    assert chain.audit_status_from_result(
        "BLOCKED: shell execution is disabled in settings") == "refused"
    assert chain.audit_status_from_result("FAILED: real breakage") == "failed"
    assert chain.audit_status_from_result("CANCELLED: declined") == "cancelled"
    assert chain.audit_status_from_result("OK: done") == "ok"


def test_a_blocked_execution_lands_in_the_log_as_refused(monkeypatch):
    """End to end through the real executor, not just the mapper."""
    from jarvis import primitives
    from jarvis.core.settings_store import settings

    recorded: list[dict] = []
    monkeypatch.setattr(primitives.audit.audit_log, "record",
                        lambda **kw: recorded.append(kw) or True)
    settings.set("shell.enabled", False, persist=False)
    try:
        out = primitives.execute("run_shell", {"command": "echo hi"})
    finally:
        settings.set("shell.enabled", True, persist=False)

    assert out.startswith("BLOCKED"), out
    assert recorded, "nothing was recorded"
    assert recorded[-1]["status"] == "refused", recorded[-1]


# ------------------------------------------------------- the UAC decline

def test_a_declined_elevation_is_cancelled_not_failed(monkeypatch):
    """The owner clicking No on Windows' consent dialog is a choice, not a bug.
    Signalled explicitly by apps.launch_app rather than sniffed out of the
    message text — a text match would break the moment the wording changes."""
    from jarvis import primitives
    from jarvis.primitives import apps

    monkeypatch.setattr(apps, "launch_app", lambda name: {
        "ok": False, "pid": None, "resolved": r"D:\g\game.exe", "matched": "game",
        "declined": True,
        "message": ("game.exe needs administrator permission and the Windows "
                    "prompt was declined, so it didn't start.")})

    out = primitives._run_launch_app({"name": "game"})
    assert out.startswith("CANCELLED"), out
    assert chain.audit_status_from_result(out) == "cancelled"


def test_an_ordinary_launch_failure_is_still_a_failure(monkeypatch):
    """Only the decline is reclassified. A program that genuinely won't start
    must stay a failure or the metric becomes flattering nonsense."""
    from jarvis import primitives
    from jarvis.primitives import apps

    monkeypatch.setattr(apps, "launch_app", lambda name: {
        "ok": False, "pid": None, "resolved": None, "matched": None,
        "message": "No application named 'nope' found."})
    out = primitives._run_launch_app({"name": "nope"})
    assert out.startswith("FAILED"), out


# --------------------------------------------------------- the report

def _rows():
    """Synthetic audit rows spanning old and recent, one of each status."""
    return [
        {"tool": "click", "status": "failed", "ts": "2026-06-01T10:00:00+00:00"},
        {"tool": "click", "status": "ok", "ts": "2026-06-01T10:01:00+00:00"},
        {"tool": "run_shell", "status": "refused", "ts": "2026-08-05T10:00:00+00:00"},
        {"tool": "launch_app", "status": "cancelled", "ts": "2026-08-05T10:01:00+00:00"},
        {"tool": "launch_app", "status": "failed", "ts": "2026-08-05T10:02:00+00:00"},
        {"tool": "launch_app", "status": "ok", "ts": "2026-08-05T10:03:00+00:00"},
    ]


def test_the_report_separates_broke_from_refused_from_declined():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "hrel", Path(__file__).resolve().parents[1] / "tests" / "harness_reliability.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    b = mod.buckets(_rows())
    assert b["broke"] == 2, b
    assert b["refused"] == 1, b
    assert b["declined"] == 1, b
    assert b["ok"] == 2, b


def test_the_report_can_window_by_date():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "hrel", Path(__file__).resolve().parents[1] / "tests" / "harness_reliability.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    recent = mod.within_days(_rows(), 30, now="2026-08-05T12:00:00+00:00")
    assert len(recent) == 4, recent
    assert all(r["ts"].startswith("2026-08") for r in recent), recent
    # The stale June click failure — the exact shape of the browse_fill and
    # click numbers that twice sent me to plan work on a dead problem.
    assert not [r for r in recent if r["tool"] == "click"], recent
