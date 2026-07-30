"""Make the repo root importable so `import jarvis` works from anywhere."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Fullscreen desktop guard (user-requested after slice 19): these modules
# drive the REAL desktop (launch/type into Notepad, a throwaway Chrome's tab
# strip, the Settings DND toggle) and steal foreground focus. Running them
# while a fullscreen app (a game) is up both interrupts the user AND flakes
# the tests — so the run refuses to start, honestly, before anything opens.
# Deterministic-only runs are never blocked.
_DESKTOP_DRIVING_MODULES = frozenset({
    "test_input", "test_tabs", "test_chain_live", "test_system",
    "test_agent_loop", "test_email_live",
})

# SHQueryUserNotificationState values meaning "the screen is claimed":
# 2 = QUNS_BUSY (fullscreen, F11-style), 3 = QUNS_RUNNING_D3D_FULL_SCREEN
# (a game), 4 = QUNS_PRESENTATION_MODE. 5 = normal desktop.
_SCREEN_CLAIMED_STATES = {2, 3, 4}


def _user_notification_state() -> int:
    """The Windows fullscreen/presentation state. JARVIS_FAKE_QUNS overrides
    for deterministic tests. Any failure reads as 'normal desktop' (5) — the
    guard must never be the thing that blocks a legitimate run."""
    fake = os.environ.get("JARVIS_FAKE_QUNS")
    if fake:
        return int(fake)
    try:
        import ctypes
        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(
                ctypes.byref(state)) == 0:  # S_OK
            return state.value
    except Exception:
        pass
    return 5


def pytest_collection_finish(session):
    picked = {item.module.__name__.rpartition(".")[2] for item in session.items
              if getattr(item, "module", None)}
    if not picked & _DESKTOP_DRIVING_MODULES:
        return  # nothing here touches the desktop — run freely
    state = _user_notification_state()
    if state in _SCREEN_CLAIMED_STATES:
        pytest.exit(
            f"a fullscreen app is up (notification state {state}) and this run "
            f"includes desktop-driving tests "
            f"({', '.join(sorted(picked & _DESKTOP_DRIVING_MODULES))}) that "
            f"would steal focus — and flake anyway. Re-run when the desktop "
            f"is free.", returncode=2)


def pytest_sessionstart(session):
    """Slice 45: install the Gemini call pacer for the whole session.

    Free-tier RPM limits produced 6-9 failures in EVERY gate run for seven
    slices — failures that were never bugs, which is how a real one gets
    dismissed as "just quota". Slice 44 proved a model fallback chain cannot fix
    it (the suite bursts past both models' buckets); pacing the calls can.

    Installed here rather than as a fixture because it patches one shared SDK
    method for the entire process, and because a session-wide window is exactly
    what a per-minute quota measures. Deterministic tests make no API calls, so
    they never sleep.
    """
    from tests import _pacer
    session.config._jarvis_pacer = _pacer.install()


def pytest_runtest_teardown(item, nextitem):
    """Backstop: no test may leave the rest of the session unpaced.

    Not hypothetical — tests/test_quota_pacer.py did exactly this once, and
    because it sorts before search_live/undo_live/vision/web_live, those ran
    unpaced and uncounted while the summary still looked plausible. That is the
    quiet failure mode this whole slice exists to prevent, so it gets a
    mechanical guard rather than a promise. Counters are preserved on re-arm.
    """
    from tests import _pacer
    pacer = getattr(item.config, "_jarvis_pacer", None)
    if pacer is None or not _pacer.rearm(pacer):
        return
    # test_quota_pacer.py installs/uninstalls the wrapper as its subject matter,
    # and this hook can run before its restoring fixture — so a re-arm there is
    # expected ordering, not a leak. Only count the ones worth chasing, or the
    # warning becomes noise and stops being read.
    module = getattr(getattr(item, "module", None), "__name__", "")
    if not module.endswith("test_quota_pacer"):
        item.config._jarvis_pacer_rearms = \
            getattr(item.config, "_jarvis_pacer_rearms", 0) + 1


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Report the COST beside the win — never hide what pacing charged us."""
    pacer = getattr(config, "_jarvis_pacer", None)
    if pacer is not None:
        terminalreporter.write_line("")
        terminalreporter.write_line(pacer.report())
        rearms = getattr(config, "_jarvis_pacer_rearms", 0)
        if rearms:
            terminalreporter.write_line(
                f"  NOTE: pacing was re-armed {rearms}x — a test tore the "
                f"wrapper off. Find it; the guard should not be load-bearing.")


@pytest.fixture(autouse=True)
def _isolated_audit_log(tmp_path, monkeypatch):
    """Slice 18: point the process-wide audit log at a per-test temp file.

    Without this, every full-suite run appends hundreds of test records —
    including live email bodies — to the REAL data/audit/ log. Splices reach
    the singleton via the module attribute (audit.audit_log), so this swap
    always intercepts. This is deliberately the only autouse fixture in
    conftest (a named deviation from the bare-conftest precedent)."""
    from jarvis.core import audit
    monkeypatch.setattr(
        audit, "audit_log", audit.AuditLog(tmp_path / "audit" / "audit.jsonl"))
