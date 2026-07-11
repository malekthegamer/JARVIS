"""Slice-8 tests: system_control — volume (pycaw), media keys, brightness.
Volume tests are LIVE against the real audio endpoint and always restore
the user's original level/mute in teardown (risk-register mitigation)."""
from __future__ import annotations

import pytest

from jarvis.primitives import system


@pytest.fixture()
def restore_volume():
    before = system.get_volume()
    assert before["ok"], f"volume readback must work on this machine: {before}"
    yield before
    system.set_volume(before["level"])
    system.set_mute(before["muted"])


def test_volume_roundtrip_restores(restore_volume):
    r = system.set_volume(37)
    assert r["ok"], r
    now = system.get_volume()
    assert now["ok"] and abs(now["level"] - 37) <= 2, now  # endpoint rounds


def test_volume_clamps_out_of_range(restore_volume):
    r = system.set_volume(150)
    assert r["ok"] and "100" in r["message"]
    assert abs(system.get_volume()["level"] - 100) <= 2
    r = system.set_volume(-5)
    assert r["ok"] and "0" in r["message"]
    assert system.get_volume()["level"] <= 2


def test_mute_roundtrip(restore_volume):
    assert system.set_mute(True)["ok"]
    assert system.get_volume()["muted"] is True
    assert system.set_mute(False)["ok"]
    assert system.get_volume()["muted"] is False


def test_unknown_media_key_fails_closed():
    r = system.media_key("self_destruct")
    assert not r["ok"]
    assert "play_pause" in r["message"]  # names the allowed keys


def test_media_key_sends():
    # play_pause twice = net no-op on any player; asserts the send path works
    assert system.media_key("play_pause")["ok"]
    assert system.media_key("play_pause")["ok"]


def test_brightness_contract_works_or_honest_failure():
    """On ANY machine: brightness either works (set->get roundtrip) or
    returns the honest unsupported message. Never raises, never lies."""
    g = system.get_brightness()
    if not g["ok"]:
        assert "support" in g["message"].lower() or "display" in g["message"].lower()
        return
    before = g["level"]
    try:
        r = system.set_brightness(max(10, min(90, before + 5)))
        assert r["ok"], r
    finally:
        system.set_brightness(before)


def test_brightness_clamps():
    r = system.set_brightness(140)
    # either honest-unsupported, or clamped to 100
    if r["ok"]:
        assert "100" in r["message"]
    else:
        assert "support" in r["message"].lower() or "display" in r["message"].lower()


# ---------- slice 12: DND / Focus Assist (drive the real Settings toggle) ----------
# Stage 0 proved the WNF write is a no-op on the user-facing toggle; the honest
# method drives the real ms-settings:notifications "Do not disturb" switch via
# UIA and CONFIRMS by readback. These deterministic tests inject a fake toggle
# through the _dnd_session() seam to pin the honesty contract with no real UI.

from contextlib import contextmanager

import pytest as _pytest


class _FakeToggle:
    """Stand-in for the real UIA ToggleSwitch wrapper.
    state() reads fresh (may raise → 'no toggle pattern'); toggle() flips it,
    unless `sticky` (models an invoke the control ignored — the sbc lesson)."""
    def __init__(self, state, *, raise_on_state=False, sticky=False):
        self._state = state
        self.raise_on_state = raise_on_state
        self.sticky = sticky
        self.toggles = 0

    def state(self):
        if self.raise_on_state:
            raise RuntimeError("no TogglePattern on this control")
        return self._state

    def toggle(self):
        self.toggles += 1
        if not self.sticky:
            self._state = 1 - self._state


def _fake_session(toggle):
    @contextmanager
    def session():
        yield toggle
    return session


def test_set_dnd_success_requires_readback(monkeypatch):
    tgl = _FakeToggle(0)
    monkeypatch.setattr(system, "_dnd_session", _fake_session(tgl))
    r = system.set_dnd(True)
    assert r["ok"] is True and r["enabled"] is True, r
    assert tgl.toggles == 1 and tgl._state == 1
    assert "readback" in r["message"].lower() or "confirmed" in r["message"].lower()


def test_set_dnd_readback_mismatch_reports_failure(monkeypatch):
    """THE sbc LESSON: the invoke 'ran' but the control never moved — this must
    report FAILURE, never a false OK."""
    tgl = _FakeToggle(0, sticky=True)
    monkeypatch.setattr(system, "_dnd_session", _fake_session(tgl))
    r = system.set_dnd(True)
    assert r["ok"] is False, r
    assert tgl.toggles == 1                       # it tried
    assert "didn't respond" in r["message"] or "did not respond" in r["message"]


def test_set_dnd_toggle_not_found_reports_unavailable(monkeypatch):
    monkeypatch.setattr(system, "_dnd_session", _fake_session(None))
    r = system.set_dnd(True)
    assert r["ok"] is False
    assert "isn't available" in r["message"] or "not available" in r["message"]


def test_set_dnd_no_toggle_pattern_reports_unavailable(monkeypatch):
    tgl = _FakeToggle(0, raise_on_state=True)
    monkeypatch.setattr(system, "_dnd_session", _fake_session(tgl))
    r = system.set_dnd(True)
    assert r["ok"] is False
    assert "available" in r["message"].lower()


def test_set_dnd_already_in_desired_state_is_ok_noop(monkeypatch):
    tgl = _FakeToggle(1)
    monkeypatch.setattr(system, "_dnd_session", _fake_session(tgl))
    r = system.set_dnd(True)
    assert r["ok"] is True and r["enabled"] is True
    assert tgl.toggles == 0, "must not invoke when already in the desired state"


def test_set_dnd_never_raises(monkeypatch):
    @contextmanager
    def boom():
        raise RuntimeError("UIA exploded")
        yield  # unreachable
    monkeypatch.setattr(system, "_dnd_session", boom)
    r = system.set_dnd(False)
    assert r["ok"] is False and isinstance(r["message"], str)


@_pytest.mark.parametrize("state,enabled", [(0, False), (1, True)])
def test_get_dnd_reads_without_changing(monkeypatch, state, enabled):
    tgl = _FakeToggle(state)
    monkeypatch.setattr(system, "_dnd_session", _fake_session(tgl))
    r = system.get_dnd()
    assert r["ok"] is True and r["enabled"] is enabled, r
    assert tgl.toggles == 0, "get_dnd must never invoke the toggle"


def test_dnd_registered_auto_tier():
    from jarvis import primitives
    for name in ("get_dnd", "set_dnd"):
        assert name in primitives.PRIMITIVES, name
        assert primitives.PRIMITIVES[name]["tier"] == "auto", name
        # AUTO verbs carry a static tier, never a classify/gate.
        assert "classify" not in primitives.PRIMITIVES[name]


def test_live_dnd_toggle_and_restore():
    """LIVE on this desktop (brightness-test pattern): DND either toggles with
    readback confirmation and restores cleanly, or reports honest-unavailable.
    Never raises, never lies. Drives the real Settings toggle."""
    before = system.get_dnd()
    if not before["ok"]:
        assert "available" in before["message"].lower()
        return
    original = before["enabled"]
    try:
        target = not original
        r = system.set_dnd(target)
        assert r["ok"] is True and r["enabled"] is target, r
        assert "readback" in r["message"].lower() or "already" in r["message"].lower()
        # independent cross-call readback proves it actually persisted
        mid = system.get_dnd()
        assert mid["ok"] and mid["enabled"] is target, mid
    finally:
        system.set_dnd(original)
    final = system.get_dnd()
    assert final["ok"] and final["enabled"] is original, final
