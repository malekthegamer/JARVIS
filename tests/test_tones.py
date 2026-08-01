"""Slice 57 — earcons. The fix for "you hear nothing at all".

Measured in stage 1: even a fast exchange is ~1.8s from the end of your sentence
to the first spoken word, and a tool chain is far longer. For that whole window
the user currently hears PURE SILENCE — grep confirmed there was no earcon, beep
or chime anywhere in the product. So the complaint "it's too slow" is partly a
complaint about not knowing whether it heard you at all.

Tones are generated as WAV bytes in pure Python (`struct`/`array`) rather than
shipped as asset files: no binaries in the repo, no new dependency, no install
step that can be skipped, and the generator is unit-testable.
"""
from __future__ import annotations

import struct

import pytest

from jarvis.voice import tones


def _parse_wav(data: bytes) -> dict:
    """Minimal RIFF/WAVE parse so the test proves the bytes are really a WAV and
    not just 'some bytes that did not raise'."""
    assert data[:4] == b"RIFF", "not a RIFF container"
    assert data[8:12] == b"WAVE", "not a WAVE file"
    assert data[12:16] == b"fmt ", "no fmt chunk"
    fmt_size = struct.unpack("<I", data[16:20])[0]
    audio_fmt, channels, rate, _byte_rate, _align, bits = struct.unpack(
        "<HHIIHH", data[20:20 + 16])
    body = data[20 + fmt_size:]
    assert body[:4] == b"data", "no data chunk"
    n_bytes = struct.unpack("<I", body[4:8])[0]
    return {"format": audio_fmt, "channels": channels, "rate": rate,
            "bits": bits, "frames": n_bytes // (bits // 8 * channels)}


def test_listening_tone_is_a_valid_pcm_wav():
    info = _parse_wav(tones.listening())
    assert info["format"] == 1, "must be uncompressed PCM (pygame loads it directly)"
    assert info["channels"] == 1
    assert info["bits"] == 16
    assert info["rate"] >= 16000
    assert info["frames"] > 0


def test_every_tone_is_short_enough_to_never_delay_capture():
    """An earcon that delays the microphone would trade one annoyance for a
    worse one. Cap it hard."""
    for name in ("listening", "thinking", "error"):
        data = getattr(tones, name)()
        info = _parse_wav(data)
        seconds = info["frames"] / info["rate"]
        assert seconds <= 0.25, f"{name} lasts {seconds:.3f}s — too long before the mic opens"


def test_tones_are_deterministic():
    """Same bytes every time — so it can be cached and so a test can compare."""
    assert tones.listening() == tones.listening()


def test_the_two_notes_actually_differ():
    """The 'listening' cue is a RISE (two distinct notes). A single flat beep
    reads as an error sound, which is the opposite of the intent."""
    a, b = tones.LISTENING_NOTES
    assert b > a, "the listening cue must rise, not fall"
    lo, hi = tones.ERROR_NOTES
    assert hi < lo, "the error cue must fall"


def test_tone_generation_never_raises_on_odd_input():
    """Never-raise contract: an earcon failure must never break the voice loop."""
    for freq, ms in ((0, 50), (-1, 50), (440, 0), (440, -5), (99999, 10)):
        out = tones._tone(freq, ms)
        assert isinstance(out, (bytes, bytearray))


# ---------------- playing them ----------------

def test_play_is_a_noop_when_disabled(monkeypatch):
    from jarvis.core.settings_store import settings
    played = []
    monkeypatch.setattr(tones.playback, "play_bytes",
                        lambda data, block=True: played.append(data))
    real = settings.get
    monkeypatch.setattr(settings, "get", lambda p, d=None:
                        (False if p == "wake.earcon" else real(p, d)))

    tones.play("listening")
    assert played == [], "earcon must respect its setting"


def test_play_never_plays_over_speech(monkeypatch):
    """RISK: earcon and speech share pygame's single music channel, so an earcon
    fired mid-reply would TRUNCATE what JARVIS is saying."""
    played = []
    monkeypatch.setattr(tones.playback, "play_bytes",
                        lambda data, block=True: played.append(data))
    monkeypatch.setattr(tones.playback, "is_playing", lambda: True)

    tones.play("listening")
    assert played == [], "must not interrupt speech that is already playing"


def test_play_swallows_audio_failures(monkeypatch):
    """A machine with no working audio device must not lose its voice loop over
    a decorative beep."""
    def boom(data, block=True):
        raise RuntimeError("no audio device")
    monkeypatch.setattr(tones.playback, "play_bytes", boom)
    monkeypatch.setattr(tones.playback, "is_playing", lambda: False)

    tones.play("listening")          # must not raise


def test_play_of_an_unknown_name_is_silent(monkeypatch):
    played = []
    monkeypatch.setattr(tones.playback, "play_bytes",
                        lambda data, block=True: played.append(data))
    monkeypatch.setattr(tones.playback, "is_playing", lambda: False)

    tones.play("no-such-tone")
    assert played == []
