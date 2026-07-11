"""Self-paced live wake-word acceptance (slice 13). NOT collected by pytest.

You control the timing — no coordination window. Run it, watch the live level
meter, say "hey Jarvis", pause, then a command. It drives the REAL path:
openWakeWord -> mic -> STT follow-up -> Gemini brain.

    python tests/harness_wake.py

Ctrl+C to stop. If the level bar barely moves while you speak, your mic is
muted or its gain is near zero (check Windows Sound settings) — that, not the
wake engine, is what stops detection.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.brain import jarvis_brain
from jarvis.voice.voice_manager import voice_manager
from jarvis.voice.wake import (DEFAULT_THRESHOLD, WakeListener, _build_model,
                               _MicSource, handle_wake)

_last_line = {"t": 0.0}


class MeteredModel:
    """Wraps the real model to print a live level + score meter, ~4x/second."""
    def __init__(self, inner):
        self.inner = inner

    def predict(self, frame):
        scores = self.inner.predict(frame)
        s = scores.get("hey_jarvis", 0.0)
        rms = float(np.sqrt(np.mean(np.asarray(frame, dtype=np.float64) ** 2)))
        now = time.time()
        if now - _last_line["t"] >= 0.25:
            _last_line["t"] = now
            bar = "#" * min(40, int(rms / 40))
            print(f"\r level |{bar:<40}| rms={rms:6.0f}  score={s:0.3f}   ",
                  end="", flush=True)
        return scores


def respond(text: str) -> None:
    print(f"\n[you said] {text!r}", flush=True)
    reply = jarvis_brain.think(text)
    print(f"[JARVIS]   {reply}", flush=True)
    voice_manager.speak(reply)


def on_wake() -> None:
    print("\n*** 'hey Jarvis' detected — listening for your command...", flush=True)
    handle_wake(listen=lambda t: voice_manager.listen(timeout=t),
                respond=respond,
                set_idle=lambda: print("(no command heard — back to idle)",
                                       flush=True),
                timeout_s=6)
    print("\n listening again — say 'hey Jarvis'...", flush=True)


def main() -> int:
    wl = WakeListener(on_wake=on_wake,
                      model=MeteredModel(_build_model()),
                      source=_MicSource(),
                      threshold=DEFAULT_THRESHOLD)
    wl.start()
    print(f"Listening for 'hey Jarvis' (threshold {DEFAULT_THRESHOLD}). "
          f"Ctrl+C to stop.\n")
    try:
        while wl.running:
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        wl.stop()
    print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
