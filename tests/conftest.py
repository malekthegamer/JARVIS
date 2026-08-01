"""Make the repo root importable so `import jarvis` works from anywhere."""
from __future__ import annotations

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

# Slice 50: ONE definition of "the screen is claimed", shared with the product.
# This used to be a copy that existed only here -- which is why the product had
# no such guard and a scheduled routine would have launched apps mid-game.
from jarvis.core.desktop import SCREEN_CLAIMED_STATES as _SCREEN_CLAIMED_STATES


from jarvis.core.desktop import notification_state as _user_notification_state


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


def pytest_sessionfinish(session, exitstatus):
    """SLICE 54 — order-independent leak detector for the server port.

    test_extension_browser.py used to start uvicorn in a daemon thread it never
    stopped, holding port 8000 for the rest of the process. test_entrypoint_
    smoke.py needs that port and passed only because alphabetical collection put
    it first — an accidental dependency that a file rename would have silently
    broken.

    That fixture now shuts its server down, and this hook makes the guarantee
    mechanical: if ANY test leaves the port bound by the end of the run, say so
    loudly and name the holder. Checking here rather than in a test keeps it
    independent of collection order, which is the entire point.
    """
    from tests._ports import port_free, port_holder

    from jarvis import config as jconfig
    if port_free(jconfig.SERVER_PORT):
        return
    holder = port_holder(jconfig.SERVER_PORT)
    # Only OUR process holding it is a leak. The owner's own running JARVIS also
    # binds this port, and the first version of this check accused a test of
    # leaking whenever JARVIS happened to be running — a false alarm that would
    # send someone hunting a bug that isn't there. port_holder() already marks
    # the current process, so use it rather than guessing.
    if "THIS pytest process" not in holder:
        return
    session.config._jarvis_port_leak = (
        f"PORT LEAK: {jconfig.SERVER_PORT} is still bound at session end by "
        f"{holder}. A test started a server and never stopped it; the next "
        f"run's real-server tests will fail with a confusing error.")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Report the COST beside the win — never hide what pacing charged us."""
    leak = getattr(config, "_jarvis_port_leak", None)
    if leak:
        terminalreporter.write_line("")
        terminalreporter.write_line(leak, red=True)
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
    always intercepts. One of exactly TWO autouse fixtures in conftest (a named
    deviation from the bare-conftest precedent); the other is
    _isolated_agent_workspace below, added by slice 54 for the same reason."""
    from jarvis.core import audit
    monkeypatch.setattr(
        audit, "audit_log", audit.AuditLog(tmp_path / "audit" / "audit.jsonl"))


@pytest.fixture(autouse=True)
def _isolated_agent_workspace(tmp_path_factory, monkeypatch):
    """Slice 54: point the workspace cage — and therefore its QUARANTINE — at a
    per-test temp dir.

    MEASURED, not assumed. Snapshotting data/ around a run showed real user
    state being mutated:

        + created  data/agent_trash/<token>/test.txt
        - DELETED  data/agent_trash/<token>/chain-gate.txt

    Tests that write into the real cage (test_chain.py, test_confirm_
    primitives.py, test_agent_loop.py, test_email_live.py) quarantine their
    deletions into the REAL data/agent_trash. That directory keeps only
    TRASH_MAX_ENTRIES (20) before `_purge_old_trash()` deletes the oldest for
    real — so a long enough test run can EVICT A USER'S RECOVERABLE FILE. The
    undo promise ("deleting quarantines it first, so it can be restored") is
    exactly what breaks.

    `files._trash_root()` derives from AGENT_FILES_DIR at call time, so
    re-pointing this one attribute isolates both the cage and the trash. Every
    consumer reads it as `files.AGENT_FILES_DIR` (module attribute), so the
    monkeypatch always intercepts — verified by grep before relying on it.

    Uses tmp_path_factory, NOT tmp_path, and that is load-bearing in two ways:

      * `tmp_path / "agent_files"` collided with the several files that define
        their own `tmp_workspace` fixture doing `.mkdir()` with no exist_ok.
      * More importantly, creating ANY directory inside a test's own tmp_path
        breaks tests that assert tmp_path is EMPTY. The full gate caught exactly
        that: test_wake.py::test_no_detection_never_calls_stt_or_writes_audio
        asserts `list(tmp_path.iterdir()) == []` to prove no audio is persisted
        before the wake word fires — a real privacy guarantee that must not be
        weakened to accommodate a fixture. An autouse fixture has no business
        putting anything in a directory the test under it may reason about.

    A test that re-points AGENT_FILES_DIR itself simply wins; this only supplies
    the default.
    """
    from jarvis.primitives import files
    ws = tmp_path_factory.mktemp("agent_ws")
    monkeypatch.setattr(files, "AGENT_FILES_DIR", ws)
