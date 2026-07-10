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
