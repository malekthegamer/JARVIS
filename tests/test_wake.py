"""Slice 13 Stage 1 — WakeListener honesty + the privacy contract.

The pre-trigger loop must run ONLY the wake model on frames it immediately
discards: never fire on_wake, never call STT, never write audio to disk, until
the wake word actually crosses threshold. And a fired wake that yields no real
follow-up utterance must return to IDLE quietly (never act on noise).

Deterministic: fakes injected for the model, the audio source, and the
follow-up listen/respond seam. No real mic, no real openWakeWord.
"""
from __future__ import annotations

import numpy as np
import pytest

from jarvis.voice.wake import WakeListener, handle_wake


class FakeModel:
    """predict(frame) -> {'hey_jarvis': score}, scores from a scripted list
    (default 0.0 = no detection). Optionally raises, to prove a per-frame
    model error is swallowed, never a trigger."""
    def __init__(self, scores=None, raises=False):
        self._scores = list(scores or [])
        self.raises = raises
        self.calls = 0

    def predict(self, frame):
        self.calls += 1
        if self.raises:
            raise RuntimeError("model boom")
        s = self._scores.pop(0) if self._scores else 0.0
        return {"hey_jarvis": s}


class FakeSource:
    """Records open/close so we can assert the single-owner mic release around
    a detection. read_frame yields silence frames."""
    def __init__(self, open_raises=False):
        self.open_raises = open_raises
        self.opened = 0
        self.closed = 0
    def open(self):
        self.opened += 1
        if self.open_raises:
            raise OSError("mic in use")
    def read_frame(self):
        return np.zeros(1280, dtype=np.int16)
    def close(self):
        self.closed += 1


def _listener(on_wake, model, source, *, cooldown_s=0.0, clock=None):
    kw = {"threshold": 0.5, "cooldown_s": cooldown_s}
    if clock is not None:
        kw["clock"] = clock
    return WakeListener(on_wake=on_wake, model=model, source=source, **kw)


# ---------------------------------------------------------------- privacy

def test_no_detection_never_calls_stt_or_writes_audio(tmp_path, monkeypatch):
    """THE privacy guarantee: over many sub-threshold frames the listener never
    fires on_wake (the ONLY path to STT), never writes any file, and never even
    releases the mic. on_wake here is a spy that would raise if the pre-trigger
    path ever reached it."""
    monkeypatch.chdir(tmp_path)
    fired = []
    model = FakeModel(scores=[0.0, 0.01, 0.2, 0.49, 0.1] * 20)   # all < 0.5
    source = FakeSource()
    wl = _listener(lambda: fired.append(1), model, source)

    for _ in range(100):
        wl._process_frame(np.zeros(1280, dtype=np.int16))

    assert fired == [], "wake fired without crossing threshold"
    assert source.closed == 0, "mic was released with no detection"
    assert model.calls == 100, "every frame should have been scored"
    assert list(tmp_path.iterdir()) == [], "no audio/file may be persisted pre-trigger"


# ---------------------------------------------------------------- detection

def test_detection_fires_on_wake_and_releases_mic():
    """On a crossing frame: on_wake fires exactly once, and the mic is released
    (close) BEFORE the follow-up and reopened after (single-owner design)."""
    fired = []
    model = FakeModel(scores=[0.92])
    source = FakeSource()
    wl = _listener(lambda: fired.append(1), model, source)

    result = wl._process_frame(np.zeros(1280, dtype=np.int16))

    assert result is True and fired == [1]
    assert source.closed == 1 and source.opened == 1, "must close then reopen the mic"


def test_rapid_retriggers_debounced():
    """Two threshold crossings within the cooldown window fire on_wake ONCE —
    a burst of over-threshold frames from one utterance is not many triggers."""
    fired = []
    now = [1000.0]
    model = FakeModel(scores=[0.9, 0.9, 0.9])
    source = FakeSource()
    wl = _listener(lambda: fired.append(1), model, source,
                   cooldown_s=2.0, clock=lambda: now[0])

    wl._process_frame(np.zeros(1280, dtype=np.int16))   # fires
    now[0] += 0.1
    wl._process_frame(np.zeros(1280, dtype=np.int16))   # within cooldown -> ignored
    now[0] += 0.1
    wl._process_frame(np.zeros(1280, dtype=np.int16))   # still within cooldown

    assert fired == [1], "cooldown must collapse a burst to a single wake"


def test_wake_loop_never_raises_on_model_error():
    """A model that throws on a frame is not a trigger and never propagates."""
    model = FakeModel(raises=True)
    source = FakeSource()
    wl = _listener(lambda: pytest.fail("must not fire on a model error"),
                   model, source)
    assert wl._process_frame(np.zeros(1280, dtype=np.int16)) is False


def test_mic_unavailable_is_honest_never_raises():
    """If the mic can't be opened, the run loop records an honest reason and
    exits — never raises, never fires."""
    model = FakeModel()
    source = FakeSource(open_raises=True)
    wl = _listener(lambda: pytest.fail("must not fire"), model, source)
    wl._run()                     # would open the source and bail
    assert wl.failed_reason and "mic" in wl.failed_reason.lower()
    assert wl.running is False


# ---------------------------------------------------------------- follow-up guard

def test_handle_wake_real_utterance_responds():
    responded = []
    idled = []
    text = handle_wake(listen=lambda t: "open notepad",
                       respond=responded.append,
                       set_idle=lambda: idled.append(1))
    assert text == "open notepad"
    assert responded == ["open notepad"] and idled == []


@pytest.mark.parametrize("heard", [None, "", "   "])
def test_handle_wake_without_utterance_returns_idle_quietly(heard):
    """False-positive guard: a wake with no real follow-up returns to IDLE and
    NEVER calls respond — noise after a mis-trigger does nothing."""
    responded = []
    idled = []
    text = handle_wake(listen=lambda t: heard,
                       respond=responded.append,
                       set_idle=lambda: idled.append(1))
    assert text is None
    assert responded == [], "must not act on an empty follow-up"
    assert idled == [1], "must return to IDLE quietly"


# ======================================================================
# STAGE 2 — server wiring: the wake callback funnels through the SAME
# _busy lock + _respond pipeline as push-to-talk (coexistence, no stacking),
# and start/stop respect the wake.enabled kill switch. No real mic/model.
# ======================================================================

def test_server_wake_dropped_while_busy(monkeypatch):
    """A wake trigger while an interaction holds _busy is DROPPED — no stacked
    follow-up capture (coexistence with PTT/chat/confirm)."""
    from jarvis import server
    from jarvis.voice.voice_manager import voice_manager
    called = []
    monkeypatch.setattr(voice_manager, "listen",
                        lambda timeout=8.0: called.append(1) or "x")
    assert server._busy.acquire(blocking=False)
    try:
        server._on_wake()
    finally:
        server._busy.release()
    assert called == [], "a wake while busy must not start a follow-up capture"


def test_server_wake_real_utterance_responds(monkeypatch):
    from jarvis import server
    from jarvis.voice.voice_manager import voice_manager
    monkeypatch.setattr(voice_manager, "listen", lambda timeout=8.0: "open notepad")
    responded = []
    monkeypatch.setattr(server, "_respond", lambda text: responded.append(text))
    server._on_wake()
    assert responded == ["open notepad"]
    assert server._busy.acquire(blocking=False), "_busy must be released after"
    server._busy.release()


def test_server_wake_empty_followup_no_respond_idles(monkeypatch):
    from jarvis import server
    from jarvis.state import AgentState, broadcaster
    from jarvis.voice.voice_manager import voice_manager
    monkeypatch.setattr(voice_manager, "listen", lambda timeout=8.0: None)
    responded = []
    monkeypatch.setattr(server, "_respond", lambda text: responded.append(text))
    server._on_wake()
    assert responded == [], "empty follow-up must not reach the brain"
    assert broadcaster.current is AgentState.IDLE


def test_start_wake_noop_when_disabled(monkeypatch):
    from jarvis import server
    from jarvis.core.settings_store import settings
    settings.set("wake.enabled", False, persist=False)
    server.stop_wake()
    server.start_wake()
    assert server.wake_running() is False, "disabled wake must not start a listener"
