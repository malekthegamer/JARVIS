"""Slice 13 Stage 3 — tray pure-logic (no GUI loop, no display).

The tray's icon loop can't run under pytest, so these cover the separable
logic: the icon image, the status-tooltip mapping, the menu construction, and
the wake toggle (which must drive server.start_wake/stop_wake AND persist the
choice). The live GUI is verified by hand in the Stage-3 acceptance.
"""
from __future__ import annotations

from jarvis import tray
from jarvis.state import AgentState


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
