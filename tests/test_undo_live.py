"""Slice 26 live test (key-gated): the REAL Gemini brain performs a volume
change and then undoes it on explicit request, with the restore verified by
an independent readback — evidence from pycaw, not prose from the model.

Two think() calls on ONE brain deliberately: the undo stack is
process-scoped, and this proves an entry pushed by one chain is reachable
from the next (the real "set… then, later, undo" shape).
"""
from __future__ import annotations

import pytest

from jarvis import config
from jarvis.brain import JarvisBrain
from jarvis.core.undo import undo_stack
from jarvis.primitives import system

live = pytest.mark.skipif(not config.get_api_key("gemini"),
                          reason="GEMINI_API_KEY not configured")


@pytest.fixture()
def restore_volume():
    before = system.get_volume()
    assert before["ok"], f"volume readback must work on this machine: {before}"
    yield before
    system.set_volume(before["level"])
    system.set_mute(before["muted"])


@live
def test_live_volume_set_then_undo_chain(restore_volume):
    """DoD: 'set my volume, then undo that' restores the exact pre-change
    level, readback-verified."""
    undo_stack.clear()
    start_level = restore_volume["level"]
    # A target the current level can't already be (so the change is real).
    target = 25 if abs(start_level - 25) > 3 else 60

    brain = JarvisBrain()
    brain.think(f"Set my volume to {target} percent.")
    mid = system.get_volume()
    assert mid["ok"] and abs(mid["level"] - target) <= 2, \
        f"the set itself must land first: {mid}"
    assert len(undo_stack) >= 1, "the volume change must leave an undo entry"

    brain.think("Undo that last volume change, please.")
    after = system.get_volume()
    assert after["ok"] and abs(after["level"] - start_level) <= 2, \
        (f"undo must restore the pre-change level {start_level}%, "
         f"readback says {after}")
