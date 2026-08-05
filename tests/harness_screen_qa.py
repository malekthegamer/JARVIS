"""Slice 47 live proof — ask about the REAL screen, right now.

The gate tests (test_vision.py::test_live_screen_query_*) use a SYNTHETIC image
so the assertion is deterministic. That proves the prompt reads a screen; it
does not prove the real capture path works on a real desktop with real
antialiasing, real fonts and real clutter. This does.

    python tests/harness_screen_qa.py
    python tests/harness_screen_qa.py "what app is in the foreground?"

Costs 1-2 Gemini calls. Reads only — captures nothing to disk unless you pass
--save, steals no focus (capture_screen does not set_focus, unlike the click
path's _grab_window).

PRIVACY, stated plainly: this sends your WHOLE screen to Gemini — every visible
window, notification and message. That is the honest cost of the feature and is
why it rides the `vision.enabled` switch.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import _harness_env  # noqa: E402,F401  (audit isolation: import BEFORE jarvis)
from jarvis import config                                   # noqa: E402
from jarvis.core.settings_store import settings             # noqa: E402
from jarvis.primitives import screen, vision                # noqa: E402

DEFAULT_QUESTIONS = [
    "What am I looking at? Describe the screen in one sentence.",
    "What application is in the foreground, and what is it showing?",
]


def main() -> int:
    if not config.get_api_key("gemini"):
        print("no GEMINI_API_KEY — nothing to prove")
        return 2
    if not settings.get("vision.enabled", True):
        print("vision.enabled is OFF — screen_query is blocked (that IS the "
              "expected behaviour; turn it on to run this harness)")
        return 2

    save = "--save" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    questions = [" ".join(args)] if args else DEFAULT_QUESTIONS

    img = screen.capture_screen()
    print(f"captured the real screen: {img.shape[1]}x{img.shape[0]}")
    print(f"max_edge = {settings.get('vision.qa_max_edge_px', 1024)} "
          f"(vision.qa_max_edge_px)\n")

    if save:
        from PIL import Image
        out = ROOT / "data" / "agent_files" / "screen_qa_capture.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(img[:, :, ::-1]).save(out)
        print(f"saved: {out}\n")

    failures = 0
    for q in questions:
        t0 = time.time()
        r = vision.answer_about_screen(q)
        took = time.time() - t0
        print(f"Q: {q}")
        if not r["ok"]:
            print(f"   FAILED ({took:.1f}s): {r['reason']}\n")
            failures += 1
            continue
        print(f"   [{took:.1f}s, {r['source']}] {r['answer']}\n")

    # And the same thing through the real primitive, so the untrusted wrapping
    # that the agent loop actually sees is visible here too.
    from jarvis import primitives
    out = primitives.execute("screen_query",
                             {"question": "In one short sentence, what is on screen?"})
    print("--- through execute() (what the model receives) ---")
    print(out[:600])
    if "UNTRUSTED" not in out:
        print("\n!! the untrusted boundary is MISSING — that is a bug")
        failures += 1

    print(f"\n{'ALL OK' if not failures else f'{failures} FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
