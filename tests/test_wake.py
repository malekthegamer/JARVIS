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

from jarvis.core import interrupt
from jarvis.voice import wake
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
    # NAMED AMENDMENT (slice 57). This used to inject a listen() that returned
    # "open notepad" FOREVER. That was harmless when a wake captured exactly one
    # utterance, but the server now opens a follow-up window, so a never-silent
    # fake conversed until max_turns and the assertion saw six replies. A real
    # user goes quiet; the fake now does too. What the test proves — a real
    # utterance reaches _respond, and _busy is released — is unchanged.
    heard = ["open notepad"]
    monkeypatch.setattr(voice_manager, "listen",
                        lambda timeout=8.0: heard.pop(0) if heard else "")
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


# ================= slice 57: the follow-up turn window =================
# THE COMPLAINT IT FIXES: every single exchange required saying "hey jarvis"
# again, because handle_wake captured exactly ONE utterance and returned. That
# is the difference between talking to someone and operating a command line by
# voice.
#
# MIC OWNERSHIP IS A NON-ISSUE, which is why this is cheap: _process_frame
# already closes the wake mic BEFORE calling _on_wake and reopens it only after
# that returns (wake.py:66-81). The turn loop therefore runs inside a window
# where the wake listener is already silent -- same thread, same _busy, zero new
# contention.

def _scripted_listen(*utterances):
    """A listen() that yields each utterance once then goes silent.

    Deliberately NOT a lambda returning the same text forever: the pre-existing
    tests do that, and with a follow-up window enabled such a fake would loop
    until max_turns. Silence-after-script is what a real user does."""
    seq = list(utterances)

    def listen(_timeout):
        return seq.pop(0) if seq else ""
    return listen


def test_follow_up_is_off_by_default_so_one_wake_is_one_turn():
    """BACK-COMPAT GUARD. follow_up_s defaults to 0, so handle_wake behaves
    exactly as it did before this slice. The pre-existing tests inject a listen()
    that returns the same text forever; had the window defaulted ON they would
    spin until max_turns and the suite would look broken for the wrong reason."""
    said = []
    wake.handle_wake(listen=lambda t: "open notepad", respond=said.append,
                     set_idle=lambda: None, timeout_s=1.0)
    assert said == ["open notepad"], said


def test_a_second_utterance_is_acted_on_without_the_wake_word():
    """THE FEATURE."""
    said = []
    wake.handle_wake(listen=_scripted_listen("what time is it", "and the date"),
                     respond=said.append, set_idle=lambda: None,
                     timeout_s=1.0, follow_up_s=5.0)
    assert said == ["what time is it", "and the date"], said


def test_silence_in_the_window_ends_the_conversation_and_idles():
    idled = []
    said = []
    wake.handle_wake(listen=_scripted_listen("hello"), respond=said.append,
                     set_idle=lambda: idled.append(True),
                     timeout_s=1.0, follow_up_s=5.0)
    assert said == ["hello"]
    assert idled, "must return to IDLE when the user stops talking"


def test_max_turns_caps_a_runaway_conversation():
    """A room with a television in it must not hold _busy forever."""
    said = []
    wake.handle_wake(listen=lambda t: "still talking", respond=said.append,
                     set_idle=lambda: None, timeout_s=1.0,
                     follow_up_s=5.0, max_turns=3)
    assert len(said) == 3, said


def test_a_long_conversation_is_capped_by_wall_clock():
    said = []
    wake.handle_wake(listen=lambda t: "more", respond=said.append,
                     set_idle=lambda: None, timeout_s=1.0, follow_up_s=5.0,
                     max_turns=100, max_total_s=0.0)
    assert len(said) == 1, "a zero budget must allow the first turn and no more"


def test_barge_in_ends_the_conversation_not_just_the_sentence():
    """RISK: without this, saying 'stop' cuts the sentence and JARVIS then
    cheerfully opens a listening window, which reads as ignoring you."""
    said = []

    def respond(text):
        said.append(text)
        interrupt.request()          # the user cut him off mid-reply

    wake.handle_wake(listen=lambda t: "keep going", respond=respond,
                     set_idle=lambda: None, timeout_s=1.0, follow_up_s=5.0)
    assert said == ["keep going"], "a barge-in must end the conversation"


def test_no_follow_up_window_when_a_confirm_is_on_screen():
    """CONFIRMING owns its own answer (Approve/Cancel). Opening a speech window
    over it would race the modal for the user's reply."""
    from jarvis.state import AgentState, broadcaster
    said = []
    prev = broadcaster.current
    try:
        def respond(text):
            said.append(text)
            broadcaster.set(AgentState.CONFIRMING)

        wake.handle_wake(listen=lambda t: "delete the file", respond=respond,
                         set_idle=lambda: None, timeout_s=1.0, follow_up_s=5.0)
        assert said == ["delete the file"], said
    finally:
        broadcaster.set(prev)


def test_the_listening_earcon_fires_before_every_capture():
    """The cue must precede the FIRST listen (so you know the wake word landed)
    and every follow-up listen (so you know it is still your turn)."""
    cues = []
    wake.handle_wake(listen=_scripted_listen("one", "two"),
                     respond=lambda t: None, set_idle=lambda: None,
                     timeout_s=1.0, follow_up_s=5.0,
                     on_listen_start=lambda: cues.append("cue"))
    assert len(cues) == 3, f"expected a cue before each of 3 captures, got {len(cues)}"


def test_handle_wake_never_raises_when_a_callback_explodes():
    """Never-raise contract: a failing interaction must not kill the listener."""
    def boom(_t):
        raise RuntimeError("mic died")
    wake.handle_wake(listen=boom, respond=lambda t: None,
                     set_idle=lambda: None, timeout_s=1.0, follow_up_s=5.0)
