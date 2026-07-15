"""Fullscreen desktop guard (post-slice-19, user-requested): a pytest run
that would drive the real desktop (Notepad/Chrome/Settings focus-steal)
must REFUSE to start while a fullscreen app is up — instead of backing the
user out of a game for 8 minutes and flaking anyway.

The Windows truth comes from SHQueryUserNotificationState; these tests use
the JARVIS_FAKE_QUNS override so they are deterministic and never depend on
what is actually on screen. Each runs a real nested pytest (subprocess) in
--collect-only mode: the guard fires before anything executes, so these are
fast and launch no apps.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)


def _run_pytest(args: list[str], quns: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, JARVIS_FAKE_QUNS=quns)
    return subprocess.run([sys.executable, "-m", "pytest", "--collect-only",
                           "-q", *args],
                          capture_output=True, text=True, cwd=ROOT, env=env,
                          timeout=120)


def test_fullscreen_blocks_desktop_driving_run():
    """QUNS 3 = a D3D fullscreen app (a game). Selecting a desktop-driving
    module must abort with the honest message, before anything runs."""
    r = _run_pytest(["tests/test_input.py"], quns="3")
    assert r.returncode != 0, r.stdout
    assert "fullscreen" in (r.stdout + r.stderr).lower(), (r.stdout, r.stderr)


def test_fullscreen_allows_deterministic_run():
    """The same fullscreen state must NOT block a run that never touches the
    desktop — inner loops stay usable while the user plays."""
    r = _run_pytest(["tests/test_state.py"], quns="3")
    assert r.returncode == 0, (r.stdout, r.stderr)


def test_idle_desktop_allows_desktop_run():
    """QUNS 5 = normal desktop, notifications accepted -> no guard."""
    r = _run_pytest(["tests/test_input.py"], quns="5")
    assert r.returncode == 0, (r.stdout, r.stderr)
