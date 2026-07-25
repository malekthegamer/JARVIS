"""Slice 13 Stage 3 — tray pure-logic (no GUI loop, no display).

The tray's icon loop can't run under pytest, so these cover the separable
logic: the icon image, the status-tooltip mapping, the menu construction, and
the wake toggle (which must drive server.start_wake/stop_wake AND persist the
choice). The live GUI is verified by hand in the Stage-3 acceptance.
"""
from __future__ import annotations

from jarvis import tray
from jarvis.state import AgentState


def test_ensure_std_streams_repairs_none_stdout(monkeypatch):
    """v1.0.4 — THE shortcut bug. pythonw.exe (what the Desktop shortcut runs)
    gives a process with sys.stdout/stderr set to None. uvicorn's log formatter
    does `sys.stdout.isatty()`, which raises AttributeError inside the server
    thread, so the server never binds and the tray reports "did not come up".
    Proven by running the real path under pythonw before this fix."""
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)
    tray._ensure_std_streams()
    import sys
    assert sys.stdout is not None and sys.stderr is not None
    # Must be usable the way uvicorn uses them, not merely non-None. The
    # isatty() call is the exact line that crashed (uvicorn/logging.py:42);
    # its VALUE doesn't matter (on Windows NUL reports True), only that it
    # returns instead of raising AttributeError.
    assert isinstance(sys.stdout.isatty(), bool)
    assert isinstance(sys.stderr.isatty(), bool)
    sys.stdout.write("probe")          # must not raise
    sys.stdout.flush()


def test_ensure_std_streams_leaves_real_streams_alone(monkeypatch):
    """A console run must keep its real stdout — the repair is only for the
    pythonw case, never a silent replacement of working streams."""
    import io
    import sys
    real = io.StringIO()
    monkeypatch.setattr("sys.stdout", real)
    monkeypatch.setattr("sys.stderr", real)
    tray._ensure_std_streams()
    assert sys.stdout is real and sys.stderr is real


def test_run_guarded_logs_a_crash_instead_of_failing_silently(monkeypatch, tmp_path):
    """The shortcut runs pythonw.exe (no console), so a startup crash used to
    vanish — the 'shortcut does nothing' bug. run_guarded must write the
    traceback to data/tray_error.log AND re-raise (so console callers see it)."""
    from jarvis import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    def _boom():
        raise RuntimeError("SENTINEL boom")

    # Suppress the Windows dialog so the test doesn't block on CI/headless.
    import ctypes
    monkeypatch.setattr(ctypes, "windll", None, raising=False)

    import pytest
    with pytest.raises(RuntimeError, match="SENTINEL boom"):
        tray.run_guarded(_boom)

    log = tmp_path / "tray_error.log"
    assert log.exists(), "a silent-launch crash must be written to a log"
    assert "SENTINEL boom" in log.read_text(encoding="utf-8")


def test_run_guarded_passes_through_on_success(monkeypatch, tmp_path):
    from jarvis import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    ran = []
    tray.run_guarded(lambda: ran.append(True))
    assert ran == [True]
    assert not (tmp_path / "tray_error.log").exists()


# ---------- v1.0.6: "JARVIS could not start" on EVERY boot ----------
#
# The launcher gave the server a fixed 15s to answer. That constant was a GUESS
# at cold-start cost, and it was wrong. Measured on one idle machine, minutes
# apart, same command:
#     first launch (cold imports)  17.6s  -> FAILED the 15s deadline
#     second launch (warm)          3.3s  -> passed
# Every boot is cold BY DEFINITION (and competes with every other startup app),
# so autostart failed every single time, while double-clicking the shortcut
# later always worked. Worse, main() then RAISED, which killed the process —
# so JARVIS never came up at all.
#
# The fix is to stop timing and start observing: wait while the server thread
# is ALIVE, fail immediately when it DIES. A slow start is not a failure.

class _FakeThread:
    def __init__(self, alive: bool = True):
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


def test_wait_for_server_keeps_waiting_while_the_thread_is_alive(monkeypatch):
    """A cold start slower than the old 15s must still succeed."""
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    calls = {"n": 0}

    def _fake_urlopen(url, timeout=1):
        calls["n"] += 1
        if calls["n"] < 70:          # ~21s of real polling — past the old 15s
            raise OSError("connection refused - still importing")
        return object()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert tray._wait_for_server(thread=_FakeThread(alive=True)) is True


def test_wait_for_server_gives_up_immediately_when_the_thread_dies(monkeypatch):
    """A DEAD server thread means waiting longer is pointless — the old code
    sat out the whole deadline either way, which is why a real crash and a slow
    start produced the same useless message."""
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    polls = {"n": 0}

    def _fake_urlopen(url, timeout=1):
        polls["n"] += 1
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert tray._wait_for_server(thread=_FakeThread(alive=False)) is False
    assert polls["n"] <= 3, f"should not keep polling a dead thread ({polls['n']} polls)"


def test_server_thread_crash_is_written_to_a_log(monkeypatch, tmp_path):
    """v1.0.4's stated lesson — 'a daemon thread swallows tracebacks, capture
    it to a file' — was never actually implemented for the server thread.
    _ensure_std_streams points stderr at os.devnull, so the traceback went
    NOWHERE and 'did startup crash?' was unanswerable by construction."""
    from jarvis import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tray, "_server_error", None, raising=False)

    import uvicorn
    monkeypatch.setattr(uvicorn, "run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SENTINEL bind fail")))

    tray._run_server()          # must NOT raise out of the thread

    log = tmp_path / "server_error.log"
    assert log.exists(), "the server thread's traceback must reach a file"
    assert "SENTINEL bind fail" in log.read_text(encoding="utf-8")
    assert "SENTINEL bind fail" in (tray._server_error or "")


def test_startup_failure_reports_the_thread_traceback_not_a_guess(monkeypatch):
    """The old message GUESSED: 'is something already using port 8000, or did
    startup crash?'. When we know, we must say."""
    monkeypatch.setattr(tray, "_server_error", "Traceback: SENTINEL real cause",
                        raising=False)
    reason = tray._startup_failure_reason()
    assert "SENTINEL real cause" in reason
    assert "is something already using" not in reason


def test_startup_failure_names_the_process_holding_the_port(monkeypatch):
    """If the port really is taken, name the culprit instead of asking the
    user to guess."""
    monkeypatch.setattr(tray, "_server_error", None, raising=False)
    monkeypatch.setattr(tray, "_port_holder", lambda: "SignalRgb.exe (pid 4242)")
    reason = tray._startup_failure_reason()
    assert "SignalRgb.exe" in reason and "4242" in reason


def test_startup_failure_says_slow_when_nothing_is_actually_wrong(monkeypatch):
    """Thread alive, port free, still no answer: say THAT, don't invent a
    cause."""
    monkeypatch.setattr(tray, "_server_error", None, raising=False)
    monkeypatch.setattr(tray, "_port_holder", lambda: None)
    reason = tray._startup_failure_reason().lower()
    assert "slow" in reason or "still starting" in reason


def test_make_icon_image_returns_image():
    img = tray._make_icon_image()
    assert img.size == (64, 64)
    assert img.mode == "RGBA"


def test_status_text_maps_states():
    assert "online" in tray._status_text(AgentState.IDLE)
    assert "listening" in tray._status_text(AgentState.LISTENING).lower()
    assert "thinking" in tray._status_text(AgentState.THINKING).lower()
    assert "speaking" in tray._status_text(AgentState.SPEAKING).lower()
    # every state maps to *something*, never blank
    for st in AgentState:
        assert tray._status_text(st).strip()


def test_build_menu_without_display():
    menu = tray.build_menu()
    labels = [str(item.text) for item in menu]
    assert any("HUD" in l for l in labels)
    assert any("Wake" in l for l in labels)
    assert any("Quit" in l for l in labels)
    # slice 39: the user needs a one-click way to start the browser JARVIS can
    # actually drive — the ordinary Chrome icon opens the Default profile,
    # which Chrome 136+ will not expose over the debug protocol.
    assert any("browser" in l.lower() for l in labels), labels


def test_open_my_browser_delegates_and_never_raises(monkeypatch):
    from jarvis.primitives import web
    called = []
    monkeypatch.setattr(web, "launch_daily_browser",
                        lambda: called.append(True) or {"ok": True, "message": "x"})
    tray.open_my_browser()
    assert called == [True]

    def _boom():
        raise RuntimeError("chrome exploded")

    monkeypatch.setattr(web, "launch_daily_browser", _boom)
    tray.open_my_browser()   # must swallow: a tray click must never kill the loop


def test_toggle_wake_invokes_start_stop_and_persists(monkeypatch):
    from jarvis import server
    from jarvis.core.settings_store import settings

    calls = []
    state = {"running": False}
    monkeypatch.setattr(server, "wake_running", lambda: state["running"])
    monkeypatch.setattr(server, "start_wake",
                        lambda: (calls.append("start"), state.update(running=True)))
    monkeypatch.setattr(server, "stop_wake",
                        lambda: (calls.append("stop"), state.update(running=False)))
    persisted = {}
    monkeypatch.setattr(settings, "set",
                        lambda k, v, **kw: persisted.__setitem__(k, v))

    # off -> on: persists enabled=True and starts
    tray.toggle_wake()
    assert calls == ["start"] and persisted["wake.enabled"] is True

    # on -> off: persists enabled=False and stops
    tray.toggle_wake()
    assert calls == ["start", "stop"] and persisted["wake.enabled"] is False


def test_autostart_command_prefers_the_venv_interpreter(monkeypatch, tmp_path):
    """Secondary bug found in the same investigation: the HKCU Run value was
    built from sys.executable, so enabling autostart from a global-Python run
    pinned autostart to GLOBAL Python while the Desktop shortcut used .venv —
    two different environments for the same app. If that global Python is ever
    3.13, autostart silently loses all voice (see test_installer)."""
    from jarvis import config
    from jarvis.core import autostart

    venv_pythonw = tmp_path / ".venv" / "Scripts" / "pythonw.exe"
    venv_pythonw.parent.mkdir(parents=True)
    venv_pythonw.write_bytes(b"")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)

    cmd = autostart._command()
    assert str(venv_pythonw) in cmd, f"autostart must target the venv: {cmd}"
    assert "tray_start.pyw" in cmd
