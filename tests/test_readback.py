"""Slice 68 — stop saying "couldn't confirm" about work that demonstrably happened.

Found by the slice-65 gate, whose one failure I captured rather than waving off:

    AssertionError: OK: Typed 20 characters.
                    VERIFY: control doesn't expose text - couldn't confirm.

The launch worked and the typing worked — 20 characters really went in. Only the
UIA readback failed, and JARVIS then told the user it couldn't confirm. That is
the same defect class as slices 63/64/67: reporting something untrue about the
user's own request, this time by understating success.

Two causes in read_back_text(), which did exactly one pass over the window's
descendants the instant typing finished:

  * Win11's WinUI Notepad updates its UIA text ASYNCHRONOUSLY, so a single
    immediate read routinely finds nothing;
  * it returned the FIRST edit-type control it saw, which in a window with a
    search box or a status field is not the one that was typed into.

Everything here fakes the UIA layer — no desktop, no Notepad. The live path is
covered by the existing tests/test_input.py.
"""
from __future__ import annotations

import pytest

from jarvis.primitives import input as jinput


class _Ctl:
    def __init__(self, text, ctype="Edit"):
        self._text = text

        class _Info:
            control_type = ctype
        self.element_info = _Info()

    def window_text(self):
        return self._text


class _Win:
    """A window whose controls change between reads, like a real async UI."""

    def __init__(self, frames):
        self._frames = list(frames)

    def descendants(self):
        frame = self._frames[0]
        if len(self._frames) > 1:
            self._frames.pop(0)
        return frame


@pytest.fixture()
def window(monkeypatch):
    def install(frames):
        # ONE window instance shared across every poll. My first version built a
        # fresh _Win per call, so it restarted at the empty frame forever and
        # the "updates late" test could never pass — the fake was wrong, not the
        # code under test.
        win = _Win(frames)
        monkeypatch.setattr(jinput, "_target_window",
                            lambda hint=None: (win, "Fake"))
        return win
    return install


def test_readback_waits_for_a_control_that_updates_late(window):
    """The captured failure: nothing readable on the first pass, the real text
    a moment later."""
    window([[], [], [_Ctl("chain proof abcd1234")]])
    got = jinput.read_back_text(None, want="chain proof abcd1234", timeout_s=2.0)
    assert got == "chain proof abcd1234", got


def test_readback_prefers_the_control_holding_the_typed_text(window):
    """A search box listed before the document must not win."""
    window([[_Ctl(""), _Ctl("Search"), _Ctl("hello world")]])
    got = jinput.read_back_text(None, want="hello world", timeout_s=0.5)
    assert got == "hello world", got


def test_readback_returns_none_when_nothing_exposes_text(window):
    """The honest case must survive — a control that genuinely exposes nothing
    still reports 'couldn't confirm' rather than inventing a pass."""
    window([[]])
    assert jinput.read_back_text(None, want="anything", timeout_s=0.3) is None


def test_readback_without_a_wanted_string_still_works(window):
    """Backward compatible: the old single-argument call still returns text."""
    window([[_Ctl("some text")]])
    assert jinput.read_back_text(None) == "some text"


def test_readback_gives_up_and_does_not_hang(window):
    """A permanently empty window must cost the timeout, not the chain."""
    import time
    window([[]])
    t0 = time.time()
    jinput.read_back_text(None, want="never", timeout_s=0.4)
    assert time.time() - t0 < 2.0


def test_type_text_confirms_when_the_control_is_slow(monkeypatch, window):
    """End to end through the executor wrapper — the report the model sees."""
    from jarvis import primitives

    monkeypatch.setattr(primitives.jinput, "type_text",
                        lambda text, window_hint=None: {
                            "ok": True, "typed": text,
                            "message": f"Typed {len(text)} characters."})
    window([[], [_Ctl("marker 42")]])
    monkeypatch.setattr(primitives.jinput, "_target_window",
                        jinput._target_window)

    out = primitives._run_type_text({"text": "marker 42", "window": "Fake"})
    assert "confirmed present" in out, out
    assert "couldn't confirm" not in out, out


def test_type_text_still_admits_when_it_really_cannot_confirm(monkeypatch, window):
    """The fix must not turn an honest 'couldn't confirm' into a false pass."""
    from jarvis import primitives

    monkeypatch.setattr(primitives.jinput, "type_text",
                        lambda text, window_hint=None: {
                            "ok": True, "typed": text,
                            "message": f"Typed {len(text)} characters."})
    window([[]])
    out = primitives._run_type_text({"text": "marker 42", "window": "Fake"})
    assert "couldn't confirm" in out, out
