"""Slice 63 — launch_app must actually launch elevated programs.

The owner said "it can't open most games on my desktop". It was never a
discovery problem: resolve_app finds the right .exe and the file exists.
launch_app used subprocess.Popen, which calls CreateProcess, which CANNOT
elevate — it just fails. Verbatim, from the real audit log:

    FAILED: Couldn't launch 'Forza Horizon 6':
            [WinError 740] The requested operation requires elevation

Entries in this machine's HKCU AppCompatFlags\\Layers carry RUNASADMIN
(forzahorizon6.exe, Spider-Man2.exe, NeedForSpeedHeat.exe, re3.exe/re4.exe,
blender-launcher.exe). Only ShellExecute honours that flag.

Corrected in slice 64: this docstring first said "26 games". That registry key
is a GRAVEYARD, not an inventory — it keeps entries for games since
uninstalled. The real split is 9 still installed, 17 stale.

Measured in the stage-0 probe, on C:\\Windows\\regedit.exe:
    Popen          -> WinError 740, immediately
    os.startfile   -> returned OK after 10.1s   (blocked on the UAC prompt)
and, when the prompt was ignored, os.startfile blocked past 120 seconds. That
second number is why the shell call is bounded on a worker thread: a launch
nobody approves must not freeze the whole chain.

Nothing here launches anything real — Popen and startfile are both faked.
"""
from __future__ import annotations

import os

import pytest

from jarvis.primitives import apps

FAKE_EXE = r"D:\Games\Forza Horizon 6\forzahorizon6.exe"
ELEVATION_REQUIRED = 740
USER_DECLINED = 1223


@pytest.fixture()
def resolved(monkeypatch):
    """Pin resolution so these tests are about LAUNCHING, not finding."""
    monkeypatch.setattr(apps, "resolve_app",
                        lambda name: (FAKE_EXE, "Forza Horizon 6"))
    monkeypatch.setattr(apps.os.path, "isdir", lambda p: False)
    return FAKE_EXE


def _oserror(winerror: int, msg: str) -> OSError:
    e = OSError(msg)
    e.winerror = winerror
    return e


# --------------------------------------------------------------- the fix

def test_an_elevation_error_falls_back_to_the_shell(resolved, monkeypatch):
    """The whole bug in one test: Popen says 740, so we must go through
    ShellExecute instead of reporting failure."""
    def popen(*a, **kw):
        raise _oserror(ELEVATION_REQUIRED, "The requested operation requires elevation")
    started: list[tuple] = []
    monkeypatch.setattr(apps.subprocess, "Popen", popen)
    monkeypatch.setattr(apps.os, "startfile",
                        lambda p, **kw: started.append((p, kw)))

    r = apps.launch_app("forza horizon 6")

    assert started, "never fell back to the shell — this is the reported bug"
    assert started[0][0] == FAKE_EXE
    assert r["ok"] is True, r
    assert "administrator" in r["message"].lower(), r["message"]


def test_a_declined_uac_is_reported_as_declined_not_missing(resolved, monkeypatch):
    """The model told the user 'Forza Horizon 6 doesn't appear to be installed,
    sir, and the request to launch it was denied.' It IS installed and nothing
    denied it. The message must state the real reason and nothing else."""
    monkeypatch.setattr(apps.subprocess, "Popen",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            _oserror(ELEVATION_REQUIRED, "requires elevation")))

    def startfile(p, **kw):
        raise _oserror(USER_DECLINED, "The operation was canceled by the user")
    monkeypatch.setattr(apps.os, "startfile", startfile)

    r = apps.launch_app("forza horizon 6")

    assert r["ok"] is False
    msg = r["message"].lower()
    assert "declin" in msg or "cancel" in msg, r["message"]
    assert "not installed" not in msg and "no application named" not in msg, r["message"]
    assert "winerror" not in msg, "raw error codes are not an explanation"


def test_the_working_directory_is_the_executables_folder(resolved, monkeypatch):
    """Games routinely fail when started from somewhere else; today they
    inherit JARVIS's own directory."""
    seen: dict = {}

    class P:
        pid = 4321
    monkeypatch.setattr(apps.subprocess, "Popen",
                        lambda a, **kw: seen.update(kw) or P())

    apps.launch_app("forza horizon 6")
    assert seen.get("cwd") == os.path.dirname(FAKE_EXE), seen


def test_a_normal_app_still_uses_popen_and_reports_a_pid(resolved, monkeypatch):
    """No regression for the ordinary case: Popen gives a real pid and we keep
    reporting it. The shell path must not be taken."""
    class P:
        pid = 9182
    monkeypatch.setattr(apps.subprocess, "Popen", lambda a, **kw: P())
    monkeypatch.setattr(apps.os, "startfile",
                        lambda *a, **kw: pytest.fail("used the shell needlessly"))

    r = apps.launch_app("forza horizon 6")
    assert r["ok"] is True and r["pid"] == 9182, r


def test_a_non_elevation_oserror_is_not_swallowed(resolved, monkeypatch):
    """Only 740 means 'try the shell'. Any other OSError is a real failure and
    must be reported, not retried into a confusing second error."""
    monkeypatch.setattr(apps.subprocess, "Popen",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            _oserror(5, "Access is denied")))
    monkeypatch.setattr(apps.os, "startfile",
                        lambda *a, **kw: pytest.fail("740 is the only fallback trigger"))

    r = apps.launch_app("forza horizon 6")
    assert r["ok"] is False
    assert "access is denied" in r["message"].lower(), r["message"]


def test_an_unanswered_prompt_does_not_hang_forever(resolved, monkeypatch):
    """MEASURED in stage 0: an ignored UAC prompt blocked os.startfile past 120
    seconds. Inside the executor that freezes the chain, so the wait is bounded
    and the timeout is reported honestly — 'it may still start' is the truth,
    because the prompt is still on screen."""
    import threading
    release = threading.Event()
    monkeypatch.setattr(apps.subprocess, "Popen",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            _oserror(ELEVATION_REQUIRED, "requires elevation")))
    monkeypatch.setattr(apps.os, "startfile", lambda p, **kw: release.wait(30))
    monkeypatch.setattr(apps, "_UAC_WAIT_S", 0.5)

    r = apps.launch_app("forza horizon 6")
    release.set()

    assert r["ok"] is False, r
    msg = r["message"].lower()
    assert "administrator" in msg or "approve" in msg, r["message"]
    assert "wait" in msg or "still" in msg or "prompt" in msg, r["message"]


def test_a_missing_target_still_fails_cleanly_with_candidates(monkeypatch):
    """The slice-60 behaviour must survive: an unresolvable name offers what IS
    installed rather than dead-ending."""
    monkeypatch.setattr(apps, "resolve_app", lambda name: (None, name))
    r = apps.launch_app("definitely not a real app")
    assert r["ok"] is False
    assert "candidates" in r, r
    assert "no application named" in r["message"].lower(), r["message"]


def test_elevation_constants_are_the_real_windows_codes():
    """Guard against a typo silently disabling the whole fallback."""
    assert apps._ERROR_ELEVATION_REQUIRED == 740
    assert apps._ERROR_CANCELLED == 1223


# ------------------------------------------------- the verify path downstream

def test_an_elevated_launch_still_watches_for_the_apps_window(monkeypatch):
    """Caught while wiring stage 1, before it shipped: _run_launch_app decided
    'this was just a URI, don't look for a window' by testing `not pid`. The
    shell path also has no pid, so every elevated game would have skipped the
    window check and reported NOT CONFIRMED however well it launched. The test
    is 'did we look for forzahorizon6's window', not 'was there a pid'.
    """
    from jarvis import primitives

    monkeypatch.setitem(
        primitives.PRIMITIVES["launch_app"], "fn",
        primitives.PRIMITIVES["launch_app"]["fn"])  # keep the real wrapper
    monkeypatch.setattr(primitives.apps, "launch_app", lambda name: {
        "ok": True, "pid": None, "resolved": FAKE_EXE,
        "matched": "Forza Horizon 6",
        "message": "Launched forzahorizon6.exe as administrator."})
    monkeypatch.setattr(primitives.screen, "capture_screen", lambda: None)
    monkeypatch.setattr(primitives.screen, "screenshot_diff", lambda a, b: 0.9)
    monkeypatch.setattr(primitives.ui_tree, "window_present", lambda n: False)
    monkeypatch.setattr(primitives.ui_tree, "window_present_for_process",
                        lambda e: False)
    monkeypatch.setattr(primitives, "WINDOW_WAIT_S", 0.1)

    out = primitives._run_launch_app({"name": "forza horizon 6"})
    assert "forzahorizon6" in out, out
    assert "window titled" in out, out


def test_a_real_uri_still_skips_the_window_needle(monkeypatch):
    """The behaviour the `not pid` check was actually protecting: ms-settings:
    has no window title to look for. Keep it."""
    from jarvis import primitives

    monkeypatch.setattr(primitives.apps, "launch_app", lambda name: {
        "ok": True, "pid": None, "resolved": "ms-settings:",
        "matched": "settings", "message": "Opened ms-settings:."})
    monkeypatch.setattr(primitives.screen, "capture_screen", lambda: None)
    monkeypatch.setattr(primitives.screen, "screenshot_diff", lambda a, b: 0.9)

    out = primitives._run_launch_app({"name": "settings"})
    assert "window titled" not in out, out


# --------------------------------------------------- stage 2: honest reporting

from jarvis import config  # noqa: E402  (live gate)

live = pytest.mark.skipif(not config.get_api_key("gemini"),
                          reason="GEMINI_API_KEY not configured")


@live
def test_live_model_does_not_claim_not_installed_for_an_elevation_failure(
        monkeypatch):
    """The reported behaviour, reproduced against the real model: given a tool
    failure that says 'needs administrator permission', it told the user the
    program wasn't installed AND that the request was denied. Both false.

    Nothing launches: launch_app is stubbed to return the exact failure text the
    real code now produces.
    """
    from jarvis import primitives
    from jarvis.brain import JarvisBrain

    monkeypatch.setitem(
        primitives.PRIMITIVES["launch_app"], "fn",
        lambda args, gi=None: (
            "FAILED: forzahorizon6.exe needs administrator permission and the "
            "Windows prompt was declined, so it didn't start."))

    brain = JarvisBrain()
    reply = brain.think("open forza horizon 6").lower()
    print(f"[live] reply: {reply[:240]}")

    assert "not installed" not in reply, reply
    assert "isn't installed" not in reply, reply
    assert "doesn't appear to be installed" not in reply, reply
    assert ("administrator" in reply or "admin" in reply
            or "declin" in reply or "approv" in reply), reply
