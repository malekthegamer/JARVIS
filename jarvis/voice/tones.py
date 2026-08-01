"""Short earcons — the fix for "you hear nothing at all" (slice 57).

MEASURED (stage 1): even a fast exchange is ~1.8s from the end of your sentence
to the first spoken word, and a tool chain is far longer. For that entire window
the user heard PURE SILENCE — there was no beep, chime or cue anywhere in the
product. Half of "it's too slow" is really "I don't know whether it heard me",
and that half is fixable without making anything faster.

Tones are SYNTHESIZED as WAV bytes rather than shipped as assets: no binaries in
the repo, no extra dependency, no install step to skip, and the generator is
unit-testable. 16-bit mono PCM, which pygame loads directly.

Every tone is capped at 250ms and played BEFORE the microphone opens, so it is
out of the speakers before capture starts and can never be recorded as speech.
"""
from __future__ import annotations

import array
import math
import struct

from jarvis.core.settings_store import settings
from jarvis.voice import playback

RATE = 22050
_FADE_MS = 8          # kills the click at note edges

# A RISE says "I'm listening"; a FALL says "that didn't work". Using the same
# shape for both would make the cue meaningless.
LISTENING_NOTES = (660, 990)
THINKING_NOTES = (520,)
ERROR_NOTES = (520, 350)


def _tone(freq: float, ms: int, volume: float = 0.28) -> bytes:
    """One note as raw 16-bit mono PCM frames. Never raises: a nonsense
    frequency or duration yields silence rather than an exception, because an
    earcon must never be able to break the voice loop."""
    try:
        n = max(0, int(RATE * max(0, ms) / 1000.0))
        if n == 0 or freq <= 0:
            return b""
        fade = max(1, int(RATE * _FADE_MS / 1000.0))
        buf = array.array("h")
        for i in range(n):
            # linear fade in/out so the note starts and ends without a click
            amp = min(1.0, i / fade, (n - i) / fade)
            buf.append(int(32767 * volume * amp
                           * math.sin(2 * math.pi * freq * i / RATE)))
        return buf.tobytes()
    except Exception:
        return b""


def _wav(pcm: bytes) -> bytes:
    """Wrap PCM frames in a minimal RIFF/WAVE container."""
    bits, channels = 16, 1
    byte_rate = RATE * channels * bits // 8
    align = channels * bits // 8
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, RATE,
                                    byte_rate, align, bits)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def _sequence(notes, ms: int = 70) -> bytes:
    return _wav(b"".join(_tone(f, ms) for f in notes))


def listening() -> bytes:
    """Two-note rise: JARVIS heard you and is listening."""
    return _sequence(LISTENING_NOTES)


def thinking() -> bytes:
    """One soft tick, for a wait long enough that silence starts to worry."""
    return _sequence(THINKING_NOTES, ms=45)


def error() -> bytes:
    """Two-note fall: that didn't work."""
    return _sequence(ERROR_NOTES)


_TONES = {"listening": listening, "thinking": thinking, "error": error}


def play(name: str) -> None:
    """Play an earcon. Never raises, never interrupts speech, never blocks long.

    The is_playing() guard is load-bearing: earcons and speech share pygame's
    single music channel, so firing one mid-reply would TRUNCATE what JARVIS is
    saying. Silence is better than cutting him off.
    """
    try:
        if not settings.get("wake.earcon", True):
            return
        make = _TONES.get(name)
        if make is None:
            return
        if playback.is_playing():
            return
        playback.play_bytes(make(), block=True)
    except Exception:
        pass      # a decorative beep must never cost the voice loop
