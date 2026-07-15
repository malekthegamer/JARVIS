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
