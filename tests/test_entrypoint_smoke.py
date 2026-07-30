"""Slice 46 — test the way a USER actually starts JARVIS.

ALL FIVE post-release bugs were one class: green in the dev environment, broken
on the user's machine. The entry point had 34 tests and not one of them RAN it —
test_installer.py reads install.bat as text, test_tray.py is pure logic behind
monkeypatch, test_smoke.py is import-level. Every one of them passes on a machine
where the real launch is dead.

This file drives the actual user path:

    Desktop shortcut -> .venv\\Scripts\\pythonw.exe tray_start.pyw
                     -> run_guarded() -> server + tray -> HUD on :8000

Under **pythonw** specifically, because "pythonw has no stdout" is what caused
the v1.0.1 silent crash — running the same code under python.exe cannot catch it.

MEASURED (Stage 0, this machine): cold start 11.6s to first HTTP 200; killing the
process tree left ZERO orphan pythonw; no dialog on a clean boot. v1.0.6 shipped
a 15s timeout against a 17.6s cold start, so the deadline here is deliberately
generous — a too-tight constant is the bug this file exists to catch.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path

import psutil
import pytest

from jarvis import config
from tests._ports import busy_port_message, port_free

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
VENV_PYW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
ENTRY = ROOT / "tray_start.pyw"

HUD_URL = f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/"
STATE_URL = f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/api/state"

# Stage 0 measured 11.6s here and v1.0.6 saw 17.6s on a cold machine. 90s is not
# a guess at the real cost — it is headroom so a SLOW boot never reads as a
# BROKEN boot. Being wrong in that direction is what shipped the v1.0.6 bug.
BOOT_DEADLINE_S = 90.0

ERROR_ARTIFACTS = (config.DATA_DIR / "server_error.log",
                   config.DATA_DIR / "tray_error.log")


def _require_install():
    """A missing venv is an actionable failure, never a skip — a skip is a
    failure with better manners, and this is the one file that proves a user's
    install works at all."""
    if not VENV_PYW.exists():
        pytest.fail(f"{VENV_PYW} is missing — run install.bat first. This file "
                    f"tests the REAL user entry point and cannot fake it.")


def _pythonw_pids() -> set[int]:
    out = set()
    for p in psutil.process_iter(["pid", "name"]):
        if (p.info.get("name") or "").lower() == "pythonw.exe":
            out.add(p.info["pid"])
    return out


def _kill_tree(pid: int) -> None:
    """Kill the launched process and every child. Never waits on the process to
    exit on its own: run_guarded() shows a MODAL DIALOG on failure, so waiting
    could hang the whole suite until a human clicks OK."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    kids = parent.children(recursive=True)
    for proc in [*kids, parent]:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs([*kids, parent], timeout=20)


def _artifact_state() -> dict:
    return {p: (p.stat().st_mtime if p.exists() else None)
            for p in ERROR_ARTIFACTS}


class Boot:
    """What one real launch produced."""

    def __init__(self) -> None:
        self.elapsed: float | None = None
        self.exit_code: int | None = None   # set if the launcher died
        self.body: str = ""
        self.state_status: int | None = None
        self.state_json: dict | None = None
        self.artifacts_before: dict = {}
        self.artifacts_after: dict = {}
        self.pids_before: set[int] = set()
        self.orphans: set[int] = set()


@pytest.fixture(scope="module")
def booted():
    """Run the ENTIRE lifecycle during setup — launch, probe, kill, orphan check
    — then hand the tests a recording to assert against.

    Deliberately not launch/yield/kill-in-teardown, which is what this file did
    first and it was wrong twice: the orphan assertion ran BEFORE teardown and so
    passed against an empty set (a test that could not fail), and the still-live
    server held port 8000 against the mid-startup test. Doing the work in setup
    means every assertion reads a real post-cleanup value and the port is free
    the moment this fixture returns.
    """
    _require_install()
    if not port_free(config.SERVER_PORT):
        pytest.fail(busy_port_message(
            config.SERVER_PORT,
            "This file launches a real JARVIS, so the test must own it."))

    boot = Boot()
    boot.pids_before = _pythonw_pids()
    boot.artifacts_before = _artifact_state()

    t0 = time.time()
    proc = subprocess.Popen([str(VENV_PYW), str(ENTRY)], cwd=str(ROOT))
    try:
        while time.time() - t0 < BOOT_DEADLINE_S:
            # If the launcher already died there is nothing to wait for. Under
            # pythonw a crash is SILENT (no console, no stderr on screen) —
            # exactly the v1.0.1 bug — so catching the exit here is what turns
            # an opaque 90s timeout into "it exited with code N after 0.6s".
            if proc.poll() is not None:
                boot.exit_code = proc.returncode
                break
            try:
                with urllib.request.urlopen(HUD_URL, timeout=2) as r:
                    if r.status == 200:
                        boot.elapsed = time.time() - t0
                        boot.body = r.read(8000).decode("utf-8", "replace")
                        break
            except Exception:
                time.sleep(0.4)

        if boot.elapsed is not None:
            try:
                with urllib.request.urlopen(STATE_URL, timeout=10) as r:
                    boot.state_status = r.status
                    boot.state_json = json.loads(
                        r.read(2000).decode("utf-8", "replace"))
            except Exception:
                pass
        boot.artifacts_after = _artifact_state()
    finally:
        # ALWAYS, even if the boot failed or timed out — a red test must never
        # leave a stray pythonw holding port 8000 for the next run.
        _kill_tree(proc.pid)
        time.sleep(1.5)
        boot.orphans = _pythonw_pids() - boot.pids_before
    return boot


# ------------------------------------------------------------------ the boot
def test_the_real_entry_point_boots_and_serves_the_hud(booted):
    """DoD 1. The whole point: pythonw + tray_start.pyw + the venv actually
    produce a served HUD. This is the test that would have caught v1.0.1."""
    assert booted.exit_code is None, (
        f"the launcher EXITED with code {booted.exit_code} instead of serving "
        f"the HUD. Under pythonw a crash is silent — this is the v1.0.1 class. "
        f"Run it by hand with python.exe to see the traceback: "
        f"{VENV_PY} {ENTRY}")
    assert booted.elapsed is not None, (
        f"the real entry point never served {HUD_URL} within "
        f"{BOOT_DEADLINE_S}s — this is exactly the 'works in dev, dead for the "
        f"user' failure. Check data/tray_error.log and data/server_error.log.")
    assert "jarvis" in booted.body.lower(), \
        f"served 200 but the body is not the HUD: {booted.body[:200]!r}"
    assert len(booted.body) > 500, \
        f"HUD body suspiciously small ({len(booted.body)}b) — a stub, not the HUD"


def test_the_boot_is_not_slower_than_the_startup_timeout_allows(booted):
    """v1.0.6: a fixed 15s timeout vs a 17.6s cold start produced 'JARVIS could
    not start' on EVERY boot. Pin that the real boot fits inside the timeout the
    tray actually grants, with the measured margin visible on failure."""
    from jarvis import tray
    assert booted.elapsed is not None, "did not boot at all"
    assert booted.elapsed < tray._STARTUP_TIMEOUT_S, (
        f"boot took {booted.elapsed:.1f}s but tray._STARTUP_TIMEOUT_S is "
        f"{tray._STARTUP_TIMEOUT_S}s — users would get the startup dialog")


def test_api_state_answers_with_valid_json(booted):
    assert booted.state_status == 200, \
        f"/api/state returned {booted.state_status}, not 200"
    assert isinstance(booted.state_json, dict) and "state" in booted.state_json, \
        f"/api/state must be a JSON object with a 'state' key: {booted.state_json!r}"


def test_no_startup_error_artifact_is_written(booted):
    """The literal v1.0.6 signature: run_guarded writes these and shows the
    'JARVIS could not start' dialog. A healthy boot must touch neither."""
    changed = [p.name for p in ERROR_ARTIFACTS
               if booted.artifacts_after.get(p) != booted.artifacts_before.get(p)]
    assert not changed, (
        f"startup wrote {changed} — that is the 'JARVIS could not start' path. "
        f"Contents: "
        f"{[p.read_text(encoding='utf-8', errors='replace')[:400] for p in ERROR_ARTIFACTS if p.name in changed]}")


def test_the_launch_left_no_orphan_process(booted):
    """Runs last in the module, after the fixture's teardown has killed the
    tree, so it sees the real post-cleanup state."""
    assert not booted.orphans, (
        f"orphan pythonw left behind: {sorted(booted.orphans)} — these would "
        f"hold port 8000 and break every later run")


# ------------------------------------------------- cleanup + guard behaviour
def test_killing_mid_startup_still_leaves_no_orphan():
    """Hostile: the harder case. Kill BEFORE the boot completes — a half-started
    tree is where orphans actually come from."""
    _require_install()
    if not port_free(config.SERVER_PORT):
        pytest.fail(busy_port_message(config.SERVER_PORT, "needed to launch."))
    before = _pythonw_pids()
    proc = subprocess.Popen([str(VENV_PYW), str(ENTRY)], cwd=str(ROOT))
    time.sleep(2.0)                      # deliberately mid-startup
    _kill_tree(proc.pid)
    time.sleep(1.5)
    orphans = _pythonw_pids() - before
    assert not orphans, f"killing mid-startup orphaned {sorted(orphans)}"


def test_a_busy_port_fails_with_an_actionable_message():
    """A bare 'connection refused' costs an investigation; this text told us in
    seconds what 18 errors meant during the slice-45 full-suite run."""
    import os
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        taken = s.getsockname()[1]
        assert port_free(taken) is False, "a listening port must read as busy"
        msg = busy_port_message(taken, "why-clause")

    assert str(taken) in msg, f"must name the port: {msg}"
    assert "why-clause" in msg, f"must carry the caller's reason: {msg}"
    # We are holding it ourselves, so it must diagnose THAT cause — not tell the
    # owner to quit an app that is not the problem. The two causes need
    # different fixes, which is the whole point of naming the holder.
    assert str(os.getpid()) in msg, f"must name the holding pid: {msg}"
    assert "run this file first or alone" in msg, \
        f"self-held port must give the ordering fix, not 'quit JARVIS': {msg}"


# ------------------------------------------------------------ venv fidelity
def _venv_imports(module: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(VENV_PY), "-c", f"import {module}"],
                          capture_output=True, text=True, timeout=120)


def test_the_venv_interpreter_is_python_312():
    """install.bat gates on 3.12 because 3.13 removed audioop (PEP 594) and the
    whole voice stack dies. A venv built by the fallback `py -3` silently broke
    voice for a real user."""
    _require_install()
    out = subprocess.run([str(VENV_PY), "-c",
                          "import sys; print('%d.%d' % sys.version_info[:2])"],
                         capture_output=True, text=True, timeout=120)
    assert out.stdout.strip() == "3.12", \
        f"venv is Python {out.stdout.strip()!r}, not 3.12 — voice will be dead"


@pytest.mark.parametrize("module, bug", [
    ("speech_recognition", "STT — needs audioop, removed in 3.13 (PEP 594)"),
    ("pystray", "the tray icon — the actual entry point"),
    ("win32com.client", "DPAPI encryption, Recycle Bin, Desktop shortcut"),
    ("openwakeword", "the 'hey jarvis' wake word"),
    ("uvicorn", "the HUD server"),
])
def test_the_critical_stack_imports_in_the_venv(module, bug):
    """Imported in the VENV interpreter, not this one. The dev machine having a
    package globally is precisely why these bugs reached users."""
    _require_install()
    res = _venv_imports(module)
    assert res.returncode == 0, \
        f"`import {module}` fails in the venv — breaks {bug}\n{res.stderr[-500:]}"


def test_the_wake_word_model_file_exists_on_disk():
    """v1.0.3: openwakeword ships WITHOUT its .onnx models, so pip install alone
    left the wake-word toggle silently unable to turn on. install.bat downloads
    them — this proves the file is really there, not that the script mentions it."""
    _require_install()
    models = (ROOT / ".venv" / "Lib" / "site-packages" / "openwakeword"
              / "resources" / "models")
    assert models.is_dir(), f"{models} missing — openwakeword models never downloaded"
    hey = list(models.glob("hey_jarvis*.onnx"))
    assert hey, (f"no hey_jarvis*.onnx in {models} — the wake word cannot load. "
                 f"Present: {[p.name for p in models.glob('*.onnx')]}")
