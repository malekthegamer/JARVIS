"""Slice 57 — the mouth-to-ear harness. The number this project never had.

`harness_latency_eval.py` profiles TOOL CHAINS (model/execute/UIA/vision). Nothing
measured the conversational path, so "JARVIS is too slow" had no number attached
and any speed work would have shipped as a claim.

NOT pytest-collected (harness_ prefix): it plays real audio and, in --voice mode,
opens the real microphone.

    python tests/harness_voice_latency.py --tts            # THE Stage-2 metric
    python tests/harness_voice_latency.py --full           # + a real model call
    python tests/harness_voice_latency.py --voice          # live mic -> ear

THE METRIC THAT MATTERS (--tts): **reply-ready -> first audible word.**
Today that is the FULL synthesis of the entire reply, because
edge_tts_provider joins every stream chunk before returning. Streaming makes it
the synthesis of the first sentence only, so this number is exactly what
sentence-level pipelining is supposed to move. It needs no mic and no model, so
it is repeatable and cheap to re-run.

--voice additionally reports true mouth-to-ear, and SUBTRACTS the recognizer's
pause_threshold (0.8 s), which elapses after the user actually stopped talking.
That subtraction is stated in the output rather than silently flattering it.
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["JARVIS_VOICE_TIMING"] = "1"      # before anything imports timing

from jarvis.core import timing                                    # noqa: E402
from jarvis.voice import capture                                  # noqa: E402
from jarvis.voice.voice_manager import voice_manager              # noqa: E402

# Deliberately three lengths: a one-liner is the common case, the long one is
# where whole-reply synthesis hurts most and where streaming should win biggest.
SAMPLES = {
    "short": "Done, sir.",
    "medium": "I've turned the volume down to thirty percent and enabled do not "
              "disturb, sir.",
    "long": "I've closed the four browser tabs you asked about, sir. The invoice "
            "PDF is on your desktop, and I've drafted the email to Sam but not "
            "sent it. Do say if you'd like me to make any changes before it goes "
            "out, because once it's sent I can't take it back.",
}


def _first_audio_gap_ms() -> float | None:
    """reply-ready -> first audible word, in ms."""
    rows = timing.records()
    starts = [r["t"] for r in rows if r["name"].startswith("tts:")]
    firsts = [r["t"] for r in rows if r["name"] == "first_audio"]
    if not starts or not firsts:
        return None
    return (firsts[0] - starts[0]) * 1000.0


def _stat(label, values, unit="ms"):
    if not values:
        print(f"  {label:34s} — no data")
        return
    med = statistics.median(values)
    print(f"  {label:34s} median {med:8.1f}{unit}   "
          f"min {min(values):7.1f}   max {max(values):7.1f}   n={len(values)}")


def run_tts(reps: int) -> None:
    print(f"\n=== TTS: reply-ready -> first audible word ({reps} reps) ===")
    print("This is the number sentence-level streaming is meant to move.\n")
    results: dict[str, list[float]] = {k: [] for k in SAMPLES}
    for rep in range(reps):
        for name, text in SAMPLES.items():
            timing.reset()
            t0 = time.monotonic()
            voice_manager.speak(text)
            total = (time.monotonic() - t0) * 1000.0
            gap = _first_audio_gap_ms()
            if gap is not None:
                results[name].append(gap)
            print(f"  rep {rep+1} {name:7s} first-audio "
                  f"{'n/a' if gap is None else f'{gap:8.1f}ms'}   "
                  f"speak-total {total:8.1f}ms")
    print()
    for name in SAMPLES:
        _stat(f"TIME-TO-FIRST-AUDIO [{name}]", results[name])


def run_full(reps: int, prompt: str) -> None:
    from jarvis.brain import jarvis_brain
    print(f"\n=== FULL: think + speak ({reps} reps) ===")
    print(f"prompt: {prompt!r}\n")
    thinks, gaps = [], []
    for rep in range(reps):
        timing.reset()
        reply = jarvis_brain.think(prompt)
        voice_manager.speak(reply)
        rows = {r["name"]: r for r in timing.records() if r["ms"] is not None}
        if "think" in rows:
            thinks.append(rows["think"]["ms"])
        gap = _first_audio_gap_ms()
        if gap is not None:
            gaps.append(gap)
        print(f"  rep {rep+1}: think {rows.get('think', {}).get('ms', 0):8.1f}ms   "
              f"first-audio +{gap if gap else float('nan'):8.1f}ms   "
              f"reply={reply[:50]!r}")
    print()
    _stat("BRAIN think()", thinks)
    _stat("TIME-TO-FIRST-AUDIO", gaps)
    if thinks and gaps:
        share = statistics.median(thinks) / (statistics.median(thinks)
                                             + statistics.median(gaps)) * 100
        print(f"\n  the model is {share:.0f}% of the wait; TTS is {100-share:.0f}%")
        print("  (that split decides whether the NEXT optimisation is the brain "
              "or the voice)")


def run_voice(reps: int) -> None:
    from jarvis.brain import jarvis_brain
    from jarvis.providers import registry
    from jarvis.core.settings_store import settings

    stt = registry.get("stt", settings.get("stt.active", "google"))
    pause = 0.8      # capture.py sets recognizer.pause_threshold = 0.8
    print(f"\n=== VOICE: true mouth-to-ear ({reps} reps) ===")
    print("Speak a short command after each prompt. Ctrl+C to stop.\n")
    m2e = []
    for rep in range(reps):
        input(f"  [rep {rep+1}] press Enter, then speak... ")
        timing.reset()
        audio = capture.listen_once()
        if audio is None:
            print("    (heard nothing)")
            continue
        text = stt.transcribe(audio)
        if not text:
            print("    (unintelligible)")
            continue
        reply = jarvis_brain.think(text)
        voice_manager.speak(reply)
        raw = timing.elapsed_ms("speech_end", "first_audio")
        if raw is None:
            print("    (no first_audio mark — did TTS fail?)")
            continue
        adjusted = raw - pause * 1000.0
        m2e.append(adjusted)
        print(f"    heard {text!r}")
        print(f"    raw speech_end->first_audio {raw:8.1f}ms")
        print(f"    minus {pause*1000:.0f}ms recognizer pause  =  {adjusted:8.1f}ms "
              f"true mouth-to-ear")
    print()
    _stat("TRUE MOUTH-TO-EAR", m2e)
    print(f"\n  NOTE: {pause*1000:.0f}ms subtracted — speech_recognition returns "
          f"that long AFTER\n  you stop talking (capture.py pause_threshold). "
          f"Stated, not hidden.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tts", action="store_true", help="TTS-only (the Stage-2 metric)")
    ap.add_argument("--full", action="store_true", help="real model + TTS")
    ap.add_argument("--voice", action="store_true", help="live mic -> ear")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--prompt", default="What time is it, in one short sentence?")
    args = ap.parse_args()

    if not (args.tts or args.full or args.voice):
        args.tts = True
    try:
        if args.tts:
            run_tts(args.reps)
        if args.full:
            run_full(args.reps, args.prompt)
        if args.voice:
            run_voice(args.reps)
    except KeyboardInterrupt:
        print("\ninterrupted")
