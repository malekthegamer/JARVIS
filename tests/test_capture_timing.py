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

def test_calibration_reruns_after_the_interval():
    """THE INCONSISTENCY. Calibrating once per process means a threshold set in
    a quiet moment governs a noisy hour later (and vice versa)."""
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


def test_calibration_can_be_disabled_with_zero():
    """0 = never re-calibrate, for a user whose environment is stable and who
    would rather not pay the 0.6s recalibration pause."""
    settings.set("stt.recalibrate_every_s", 0.0, persist=False)
    capture._calibrated_index = 3
    capture._last_calibrated_at = 1000.0

    assert capture.should_calibrate(3, now=1_000_000.0) is False


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
