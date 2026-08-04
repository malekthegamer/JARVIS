"""Slice 58 — the capture timing the owner actually complained about.

REAL-USE REPORT: "the listening is kind of inconsistent. Sometimes it is in
listening mode for more than it needs to, and other times it will cut me off mid
sentence, and it doesn't really understand what I want because it cut me off."

Three causes, and the operative word is INCONSISTENT:

  1. Ambient calibration ran ONCE per device for the whole process lifetime,
     while `dynamic_energy_threshold` kept drifting from that stale baseline.
     Calibrate in a quiet moment and later speech reads as silence (early
     cut-off); calibrate during noise and speech-start is missed entirely.
  2. `pause_threshold` (the trailing silence that declares you finished) was a
     hardcoded 0.8s serving two opposite needs — short enough to clip a
     mid-thought pause, long enough to add dead air to every turn.
  3. `phrase_time_limit` was a hardcoded 15s that truncates SILENTLY: verified in
     speech_recognition, `timeout` raises WaitTimeoutError but phrase_time_limit
     just breaks and returns the partial audio. Anything longer than 15s was
     quietly halved and handed to STT with no signal at all.

Before this slice there were NO stt.* timing settings and NO tests for any of it.
These are pure/unit tests — no microphone, no network.
"""
from __future__ import annotations

import pytest

from jarvis.core.settings_store import settings
from jarvis.voice import capture


@pytest.fixture(autouse=True)
def _restore_capture_state(monkeypatch):
    """LEAK GUARD, written with the install. capture holds process-wide
    singletons (_recognizer, _calibrated_index, _last_calibrated_at) and these
    tests mutate settings that feed them."""
    keys = ("stt.pause_threshold", "stt.phrase_time_limit", "stt.listen_timeout",
            "stt.recalibrate_every_s")
    saved = {k: settings.get(k) for k in keys}
    monkeypatch.setattr(capture, "_recognizer", None, raising=False)
    monkeypatch.setattr(capture, "_recognizer_config", None, raising=False)
    monkeypatch.setattr(capture, "_calibrated_index", None, raising=False)
    monkeypatch.setattr(capture, "_last_calibrated_at", 0.0, raising=False)
    yield
    for k, v in saved.items():
        settings.set(k, v, persist=False)


# ---------------- tunability (there were no settings at all) ----------------

def test_capture_reads_its_timing_from_settings():
    settings.set("stt.pause_threshold", 1.4, persist=False)
    settings.set("stt.phrase_time_limit", 33.0, persist=False)
    settings.set("stt.listen_timeout", 9.0, persist=False)

    cfg = capture.capture_config()
    assert cfg.pause_threshold == 1.4
    assert cfg.phrase_time_limit == 33.0
    assert cfg.listen_timeout == 9.0


def test_the_default_pause_threshold_tolerates_a_mid_thought_pause():
    """The owner pauses mid-sentence. 0.8s (the old hardcoded value, and the
    library default) clips him; the default must be more forgiving than that."""
    from jarvis.core.settings_store import DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS["stt"]["pause_threshold"] >= 1.2, \
        "a mid-thought pause must not end the utterance"


def test_the_default_phrase_limit_is_longer_than_the_old_silent_15s():
    from jarvis.core.settings_store import DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS["stt"]["phrase_time_limit"] > 15.0


def test_the_recognizer_is_rebuilt_when_the_setting_changes():
    """THE BUG THIS FIXES: the recognizer was a singleton built once, so
    pause_threshold was written exactly once per process. A settings change
    could never take effect without a restart — which would have made the new
    settings a lie."""
    settings.set("stt.pause_threshold", 1.0, persist=False)
    first = capture.get_recognizer()
    assert first.pause_threshold == 1.0
    assert capture.get_recognizer() is first, "must be reused when unchanged"

    settings.set("stt.pause_threshold", 1.6, persist=False)
    second = capture.get_recognizer()
    assert second is not first, "a settings change must rebuild the recognizer"
    assert second.pause_threshold == 1.6


# ---------------- consistency: recalibration ----------------

def test_the_rate_limit_gap_is_respected():
    """Slice 59: this is a MINIMUM GAP between timeout-triggered measurements,
    so a persistently noisy room cannot recalibrate on every single turn. It is
    no longer a timer that triggers calibration by itself."""
    settings.set("stt.recalibrate_every_s", 60.0, persist=False)
    capture._calibrated_index = 3
    capture._last_calibrated_at = 1000.0

    assert capture.should_calibrate(3, now=1000.0 + 59) is False
    assert capture.should_calibrate(3, now=1000.0 + 61) is True


def test_calibration_always_runs_for_a_new_device():
    settings.set("stt.recalibrate_every_s", 3600.0, persist=False)
    capture._calibrated_index = 3
    capture._last_calibrated_at = 1000.0

    assert capture.should_calibrate(7, now=1000.1) is True, \
        "a different mic must always be re-measured"


def test_zero_means_no_rate_limit_not_never():
    """NAMED AMENDMENT (slice 59). This setting's meaning deliberately changed.

    Under slice 58 it was a SCHEDULE that triggered calibration on a timer, and
    0 meant "never fire". That schedule was the bug: it fired on the capture
    path, in front of a speaking user. Calibration is now only ever performed
    after a capture times out, so this value is a MINIMUM GAP between those
    measurements — and 0 means "no rate limit", not "never measure"."""
    settings.set("stt.recalibrate_every_s", 0.0, persist=False)
    capture._calibrated_index = 3
    capture._last_calibrated_at = 1000.0

    assert capture.should_calibrate(3, now=1_000_000.0) is True


# ---------------- honesty: the silent truncation ----------------

class _FakeAudio:
    """Mimics sr.AudioData's duration-bearing attributes."""
    def __init__(self, seconds, rate=16000, width=2):
        self.sample_rate = rate
        self.sample_width = width
        self.frame_data = b"\x00" * int(seconds * rate * width)


def test_a_full_length_capture_is_detected_as_truncated():
    """speech_recognition does NOT raise on phrase_time_limit — it breaks and
    returns the partial audio. So a capture that runs exactly to the limit is
    the only evidence that the user was cut off, and it must not be ignored."""
    assert capture.looks_truncated(_FakeAudio(30.0), limit=30.0) is True
    assert capture.looks_truncated(_FakeAudio(29.9), limit=30.0) is True


def test_a_normal_utterance_is_not_flagged():
    assert capture.looks_truncated(_FakeAudio(3.0), limit=30.0) is False


def test_truncation_detection_never_raises_on_odd_audio():
    """Never-raise contract — a detection helper must not be able to break
    capture itself."""
    class _Odd:
        pass
    for bad in (None, _Odd(), _FakeAudio(1.0, rate=0)):
        assert capture.looks_truncated(bad, limit=30.0) in (True, False)


def test_truncation_is_reported_to_the_caller(monkeypatch):
    """The user must be able to learn that they were cut off. Silently handing
    STT half a sentence is what produced 'it doesn't understand what I want'."""
    reported = []
    monkeypatch.setattr(capture, "_on_truncated", reported.append, raising=False)
    capture.note_truncation(_FakeAudio(30.0), limit=30.0)
    assert reported, "a truncated capture must surface, not vanish"


# ---------------- the follow-up window's trailing silence ----------------

def test_the_follow_up_window_is_shorter_than_the_first_turn():
    """Slice 57 gave every conversation a trailing open-mic window that did not
    exist before, contributing to "sometimes it is in listening mode for more
    than it needs to". The first utterance after a wake word is EXPECTED; a
    second one is optional, so it gets a smaller budget."""
    from jarvis.core.settings_store import DEFAULT_SETTINGS

    wake = DEFAULT_SETTINGS["wake"]
    assert wake["follow_up_window_s"] < wake["follow_up_timeout_s"], \
        "the optional follow-up must not wait as long as the expected first turn"


# ---------------- device selection: a line-in jack is not a microphone ----------------

def test_a_line_in_jack_is_not_treated_as_a_microphone():
    """FOUND BY THE GATE, on the owner's real machine: find_real_mic() picked
    'Line In (Realtek HD Audio Line input)' — index 44 — over his actual
    earphone microphone, because the ranking prefers 'realtek' and nothing
    excluded a line-input jack.

    Nothing is plugged into that jack, so the stream never opens and voice input
    silently does not work at all. Worse, whether it is picked depends on device
    enumeration, so it can flip between runs — which is indistinguishable from
    "the listening is inconsistent". A user who genuinely records from line-in
    can still pin it with stt.mic_device_index."""
    assert capture.is_probably_real_mic("Line In (Realtek HD Audio Line input)") is False
    assert capture.is_probably_real_mic("Line Input (Realtek HD Audio)") is False


def test_real_microphones_are_still_accepted():
    """The exclusion must not be so broad that it rejects actual mics."""
    for good in ("Microphone (Samsung USB C Earphones)",
                 "Microphone (Realtek HD Audio)",
                 "Headset Microphone (Bluetooth)"):
        assert capture.is_probably_real_mic(good) is True, good


# ============ slice 59: calibration must never run in front of the user ============
# THE REGRESSION SLICE 58 SHIPPED. speech_recognition's own docstring for
# adjust_for_ambient_noise says "Should be used on periods of audio WITHOUT
# speech". Slice 58 put it on the capture path, so a wake turn went:
#
#   1. earcon says "I'm listening"   -> the user starts speaking
#   2. 0.6s adjust_for_ambient_noise -> measures the USER'S VOICE as ambient
#   3. only now does listen() start
#
# The formula converges energy_threshold toward 1.5x whatever it heard, so after
# calibrating on speech the user's real voice sits BELOW threshold, never
# registers as speech-start, and listen() waits out the whole timeout with the
# mic open. That is exactly the reported symptom: "it sits listening too long".
#
# The fix: the ONLY safe moment to measure ambient noise is when a capture has
# just TIMED OUT -- the mic is already open, and nothing being heard is both
# proof of silence and evidence the threshold may be wrong. Self-correcting, and
# it can never eat speech.
#
# These tests drive the REAL listen_once() with fakes. The slice-58 tests never
# called it at all, which is exactly why none of this was caught.


class _FakeSource:
    """Stands in for sr.Microphone's context manager."""
    def __init__(self):
        self.stream = object()
        self.CHUNK = 1024
        self.SAMPLE_RATE = 16000
        self.SAMPLE_WIDTH = 2

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeRecognizer:
    def __init__(self, raise_timeout=False):
        self.pause_threshold = 1.2
        self.energy_threshold = 300
        self.dynamic_energy_threshold = True
        self.calls = []
        self._raise_timeout = raise_timeout

    def adjust_for_ambient_noise(self, source, duration=1):
        self.calls.append(("calibrate", duration))

    def listen(self, source, timeout=None, phrase_time_limit=None):
        self.calls.append(("listen", timeout, phrase_time_limit))
        if self._raise_timeout:
            import speech_recognition as sr
            raise sr.WaitTimeoutError("no speech")
        return _FakeAudio(2.0)


def _drive_listen_once(monkeypatch, recognizer, on_ready=None):
    """Run the REAL listen_once against fakes."""
    import speech_recognition as sr
    monkeypatch.setattr(capture, "get_recognizer", lambda: recognizer)
    monkeypatch.setattr(capture, "find_real_mic", lambda: (1, "Fake Mic"))
    monkeypatch.setattr(sr, "Microphone", lambda device_index=None: _FakeSource())
    monkeypatch.setattr(capture, "_calibrated_index", 1, raising=False)
    return capture.listen_once(timeout=5.0, phrase_time_limit=30.0,
                               on_ready=on_ready)


def test_calibration_does_NOT_run_before_a_normal_capture(monkeypatch):
    """THE FIX. A successful capture must never be preceded by 0.6s of
    ambient measurement — that is what swallowed the opening words and poisoned
    the threshold with the user's own voice."""
    rec = _FakeRecognizer()
    _drive_listen_once(monkeypatch, rec)

    kinds = [c[0] for c in rec.calls]
    assert "listen" in kinds, rec.calls
    assert "calibrate" not in kinds, \
        f"calibration ran in front of the user again: {rec.calls}"


def test_a_timeout_recalibrates_while_the_mic_is_still_open(monkeypatch):
    """Nothing heard = proof of silence AND evidence the threshold may be too
    high. That is the one safe moment, and the mic is already open."""
    rec = _FakeRecognizer(raise_timeout=True)
    monkeypatch.setattr(capture, "_last_calibrated_at", 0.0, raising=False)
    out = _drive_listen_once(monkeypatch, rec)

    assert out is None, "a timeout must still report silence to the caller"
    kinds = [c[0] for c in rec.calls]
    assert kinds == ["listen", "calibrate"], \
        f"expected listen-then-calibrate on timeout, got {rec.calls}"


def test_repeated_timeouts_do_not_recalibrate_every_time(monkeypatch):
    """A noisy room must not thrash: recalibrate_every_s is the MINIMUM gap
    between measurements, not a schedule for them."""
    import time as _t
    rec = _FakeRecognizer(raise_timeout=True)
    settings.set("stt.recalibrate_every_s", 999.0, persist=False)
    monkeypatch.setattr(capture, "_last_calibrated_at", _t.monotonic(),
                        raising=False)

    _drive_listen_once(monkeypatch, rec)
    assert [c[0] for c in rec.calls] == ["listen"], \
        f"recalibrated inside the rate-limit window: {rec.calls}"


def test_the_ready_cue_fires_only_after_the_microphone_is_open(monkeypatch):
    """The earcon promises a live mic. Before this it played BEFORE the device
    was opened, so everything said during enumeration/open was lost."""
    events = []
    rec = _FakeRecognizer()
    rec_listen = rec.listen

    def tracking_listen(source, timeout=None, phrase_time_limit=None):
        events.append("listen")
        return rec_listen(source, timeout=timeout,
                          phrase_time_limit=phrase_time_limit)
    rec.listen = tracking_listen

    _drive_listen_once(monkeypatch, rec, on_ready=lambda: events.append("cue"))

    assert events == ["cue", "listen"], \
        f"the cue must fire after the mic opens and before listening: {events}"


def test_a_failing_ready_cue_never_breaks_capture(monkeypatch):
    """Never-raise contract: a decorative beep must not cost an utterance."""
    def boom():
        raise RuntimeError("no audio device")
    rec = _FakeRecognizer()
    out = _drive_listen_once(monkeypatch, rec, on_ready=boom)
    assert out is not None, "a broken earcon must not lose the capture"


# ---------------- the threshold must never sit BELOW room noise ----------------

def test_calibration_never_leaves_the_threshold_under_ambient(monkeypatch):
    """A threshold below room noise is the SAME user-visible bug in the other
    direction: listen() treats ambient as speech-start, never sees "silence" to
    end the phrase, and records to phrase_time_limit (30s). Measured on the real
    machine, adjust_for_ambient_noise converged to 20 against an ambient RMS of
    ~43 — so it does undershoot in practice and must be clamped."""
    import audioop

    class _NoisySource:
        SAMPLE_RATE, SAMPLE_WIDTH, CHUNK = 16000, 2, 1024
        def __init__(self):
            # a steady tone well above any sane floor
            import struct
            self.stream = self
            self._buf = struct.pack("<1024h", *([3000] * 1024))
        def read(self, n):
            return self._buf

    class _UndershootingRecognizer:
        energy_threshold = 300
        def adjust_for_ambient_noise(self, source, duration=1):
            self.energy_threshold = 20      # the measured real-world undershoot

    rec = _UndershootingRecognizer()
    src = _NoisySource()
    level = capture.calibrate_with_floor(rec, src)

    ambient = audioop.rms(src._buf, 2)
    assert level > ambient, \
        f"threshold {level} must sit above ambient {ambient}, or capture never ends"
    assert rec.energy_threshold >= capture._MIN_ENERGY_THRESHOLD


def test_calibration_floor_never_raises_on_a_broken_source():
    """Never-raise contract: a source that cannot be read must not break the
    timeout path it is called from."""
    class _Broken:
        def adjust_for_ambient_noise(self, source, duration=1):
            raise RuntimeError("no")
        energy_threshold = 100
    class _NoStream:
        SAMPLE_RATE, SAMPLE_WIDTH, CHUNK = 16000, 2, 1024
        stream = None
    assert capture.calibrate_with_floor(_Broken(), _NoStream()) >= 0
