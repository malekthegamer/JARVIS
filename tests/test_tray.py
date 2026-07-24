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
