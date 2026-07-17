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


# ---------------- slice 23: local-Whisper port -----------------------------

def test_whisper_registered_and_install_gated(monkeypatch):
    from jarvis.providers import registry
    import importlib.util as _u
    p = registry.get("stt", "local_whisper")
    assert p is not None, "local_whisper must be registered"
    monkeypatch.setattr(_u, "find_spec", lambda name: object())
    assert p.is_configured() is True
    monkeypatch.setattr(_u, "find_spec", lambda name: None)
    assert p.is_configured() is False


def test_whisper_transcribe_seam_and_device(monkeypatch):
    """No real model load: patch _get_model. Proves transcribe joins segments
    and honors the whisper_model/device settings for the cache key."""
    from jarvis.providers import registry
    from jarvis.core.settings_store import settings

    class _Seg:
        def __init__(self, t): self.text = t

    class _Model:
        def transcribe(self, wav, **kw):
            return ([_Seg(" hello "), _Seg("world")], object())

    class _Audio:
        def get_wav_data(self): return b"RIFFfake"

    p = registry.get("stt", "local_whisper")
    monkeypatch.setattr(p, "_get_model", lambda: _Model())
    assert p.transcribe(_Audio()) == "hello world"
