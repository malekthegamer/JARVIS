"""Stage-4 exit tests (automated half): the machine has input devices, the
real-mic detector picks one, and a capture stream opens and closes cleanly.
The live-utterance half of the exit criterion needs a human voice."""
from __future__ import annotations

import pytest

from jarvis.voice import capture


def test_input_devices_exist():
    devices = capture.list_input_devices()
    assert devices, "no audio input devices found at all"


def test_find_real_mic_returns_usable_choice():
    index, name = capture.find_real_mic()
    assert name  # never empty — falls back to "system default"
    if index is not None:
        devices = dict(capture.list_input_devices())
        assert index in devices


def test_capture_stream_opens_and_closes():
    import speech_recognition as sr
    index, name = capture.find_real_mic()
    try:
        with sr.Microphone(device_index=index) as source:
            assert source.stream is not None
    except OSError as exc:
        pytest.fail(f"could not open capture stream on '{name}': {exc}")
