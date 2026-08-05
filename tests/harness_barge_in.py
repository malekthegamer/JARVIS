"""Slice 49 live proof — does the audio ACTUALLY stop mid-sentence?

The unit tests mock playback, which proves the wiring but not the thing the user
cares about: that JARVIS shuts up when told. This plays real speech through real
speakers and measures the wall clock.

    python tests/harness_barge_in.py

Checks:
  1. a long utterance played uninterrupted takes ~N seconds (the baseline)
  2. interrupting ~1s in returns FAR sooner  -- the actual barge-in
  3. audio still works afterwards           -- the mixer is not wedged (risk 6)

Makes noise. Nothing is installed, changed or left behind.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import _harness_env  # noqa: E402,F401  (audit isolation: import BEFORE jarvis)
from jarvis.core import chain, interrupt          # noqa: E402
from jarvis.core.settings_store import settings   # noqa: E402
from jarvis.voice import playback                 # noqa: E402

LONG = ("This is a deliberately long sentence, sir, so that there is plenty of "
        "time to interrupt me before I reach the end of it. I shall keep "
        "talking for quite a while yet, describing nothing in particular, "
        "purely so that the measurement has room to breathe.")
SHORT = "Audio still works."

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def synth(text: str) -> bytes:
    import asyncio

    import edge_tts
    voice = settings.get("tts.edge_voice", "en-GB-RyanNeural")

    async def _collect() -> bytes:
        out = b""
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk.get("type") == "audio":
                out += chunk["data"]
        return out
    return asyncio.run(_collect())


def play_timed(data: bytes) -> float:
    t0 = time.time()
    playback.play_bytes(data)
    return time.time() - t0


def main() -> int:
    print("synthesising...")
    try:
        long_mp3 = synth(LONG)
        short_mp3 = synth(SHORT)
    except Exception as exc:
        print(f"TTS unavailable: {exc}")
        return 2

    # 1. baseline — how long does it take uninterrupted?
    print("\n[1/3] playing the full utterance (baseline)...")
    full = play_timed(long_mp3)
    print(f"      full playback = {full:.1f}s")
    if full < 3:
        print("      utterance too short to prove anything — aborting")
        return 2

    # 2. the real thing — interrupt ~1s in
    print("\n[2/3] playing again, interrupting after 1.0s...")
    tracker = chain.start()
    threading.Timer(1.0, interrupt.request).start()
    cut = play_timed(long_mp3)
    print(f"      interrupted playback = {cut:.1f}s (was {full:.1f}s)")

    check("audio_actually_stopped_early", cut < full * 0.5,
          f"{cut:.1f}s vs {full:.1f}s full")
    check("stopped_close_to_when_asked", cut < 2.5, f"{cut:.1f}s (asked at 1.0s)")
    check("the_chain_was_marked_interrupted", tracker.aborted == "interrupted",
          f"aborted={tracker.aborted!r}")

    refusal = tracker.pre_call_guard("launch_app", {"name": "notepad"})
    check("further_steps_are_refused", refusal is not None)
    check("the_refusal_names_the_real_reason",
          bool(refusal) and "interrupt" in refusal.lower(),
          (refusal or "")[:80])
    chain.clear("interrupted")

    # 3. the mixer must not be wedged by an abrupt stop
    print("\n[3/3] speaking again after the interrupt...")
    again = play_timed(short_mp3)
    check("audio_works_after_an_interrupt", again > 0.4, f"{again:.1f}s")

    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
