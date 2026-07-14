"""Slice-17 evaluator — does pre-click verification actually stop the wrong click,
and what does it COST?

Slice 16 measured vision labelling a control correctly while POINTING at its
neighbour. This scores the fix against ground truth on closely-spaced icons:

  mis-localization rate : vision pointed at the wrong control   (a property of vision)
  catch rate            : REFUSED / mis-localized               -> want 1.0
  FALSE-REFUSAL rate    : REFUSED / correctly-localized         -> want 0.0  (the cost)
  wrong-click rate      : mis-localized AND allowed  == THE BOTTOM LINE
                          before: == mis-localization rate  ->  after: ~0
  latency / extra calls : cost of the check (0 extra when UIA can name it)

A verifier that refuses EVERYTHING has a perfect catch rate and is useless, so
both rates are always reported. NOT collected by pytest (harness_ prefix).

    python tests/harness_click_verify_eval.py --reps 2 --verify 0 --out before.json
    python tests/harness_click_verify_eval.py --reps 2 --verify 1 --out after.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from jarvis import config
from jarvis.core.settings_store import settings
from jarvis.primitives import vision

WINDOW = "VisionPad"

# Every icon on the toolbar — adjacent neighbours are the whole point.
CASES = [
    ("the new document icon", "new"),
    ("the open folder icon", "open"),
    ("the save icon", "save"),
    ("the save-as icon (floppy with a pencil)", "save_as"),
    ("the print icon", "print"),
    ("the cut (scissors) icon", "cut"),
    ("the copy icon", "copy"),
    ("the paste icon", "paste"),
    ("the undo arrow", "undo"),
    ("the redo arrow", "redo"),
    ("the delete / trash icon", "trash"),
    ("the send (paper plane) icon", "send"),
]


def _count_calls():
    counter = {"n": 0}
    for name in ("_call_gemini", "_call_verify_json"):
        fn = getattr(vision, name, None)
        if fn is None or getattr(fn, "_counted", False):
            continue

        def make(orig):
            def counted(*a, **k):
                counter["n"] += 1
                return orig(*a, **k)
            counted._counted = True
            return counted
        setattr(vision, name, make(fn))
    return counter


def run_phase(pad_flag, label, reps, counter, verify_on):
    tmp = Path(__file__).resolve().parent / "_clickverify_manifest.json"
    tmp.unlink(missing_ok=True)
    cmd = [sys.executable, str(ROOT / "tests" / "harness_visionpad.py"), str(tmp)]
    if pad_flag:
        cmd.append(pad_flag)
    pad = subprocess.Popen(cmd)
    rows = []
    try:
        dl = time.time() + 20
        while time.time() < dl and not tmp.exists():
            time.sleep(0.3)
        time.sleep(1.0)
        m = json.loads(tmp.read_text(encoding="utf-8"))
        ox, oy = m["origin"]
        truth = {k: (ox + c["rect"][0], oy + c["rect"][1],
                     ox + c["rect"][2], oy + c["rect"][3])
                 for k, c in m["controls"].items()}

        print(f"\n=== {label} (verify={'ON' if verify_on else 'OFF'}) ===")
        for desc, expect in CASES:
            for _ in range(reps):
                counter["n"] = 0
                t0 = time.time()
                r = vision.locate_and_classify(desc, window_hint=WINDOW)
                if not r.get("ok"):
                    continue
                px, py = r["point"]
                x0, y0, x1, y1 = truth[expect]
                correct = x0 <= px <= x1 and y0 <= py <= y1
                landed = next((k for k, (a, b, c, d) in truth.items()
                               if a <= px <= c and b <= py <= d), "nothing")

                # would the click actually fire?
                if verify_on:
                    v = vision.verify_point((px, py), WINDOW, r["label"])
                    allowed = bool(v.get("verified"))
                    actual = v.get("actual_label", "")
                else:
                    allowed, actual = True, ""   # today: every located click fires
                ms = (time.time() - t0) * 1000
                rows.append({"case": desc, "expect": expect, "landed": landed,
                             "correct": correct, "allowed": allowed,
                             "actual": actual, "ms": ms, "calls": counter["n"]})
                mark = "OK " if correct else "MIS"
                act = "CLICK " if allowed else "REFUSE"
                flag = ""
                if not correct and allowed:
                    flag = "   <-- WRONG CLICK"
                if correct and not allowed:
                    flag = "   <-- false refusal"
                print(f"  {mark} {desc[:34]:34s} landed={landed:9s} -> {act}"
                      f"{('  actual=' + actual[:22]) if actual else ''}{flag}",
                      flush=True)
    finally:
        pad.terminate()
        try:
            pad.wait(timeout=5)
        except Exception:
            pad.kill()
        tmp.unlink(missing_ok=True)
        time.sleep(0.8)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--verify", type=int, default=1)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if not config.get_api_key("gemini"):
        print("GEMINI_API_KEY not configured."); return 1

    settings.set("vision.enabled", True, persist=False)
    settings.set("vision.verify_click_point", bool(args.verify), persist=False)
    counter = _count_calls()

    rows = []
    rows += run_phase("--hard", "HARD (gapped toolbar)", args.reps, counter, args.verify)
    rows += run_phase("--tight", "TIGHT (touching icons)", args.reps, counter, args.verify)

    n = len(rows)
    mis = [r for r in rows if not r["correct"]]
    ok = [r for r in rows if r["correct"]]
    wrong_clicks = [r for r in mis if r["allowed"]]
    false_refusals = [r for r in ok if not r["allowed"]]
    summary = {
        "verify_click_point": bool(args.verify), "reps": args.reps, "n": n,
        "mis_localization_rate": round(len(mis) / n, 3) if n else 0,
        "catch_rate": round(1 - len(wrong_clicks) / len(mis), 3) if mis else None,
        "false_refusal_rate": round(len(false_refusals) / len(ok), 3) if ok else 0,
        "wrong_click_rate": round(len(wrong_clicks) / n, 3) if n else 0,
        "latency_ms_mean": round(statistics.mean([r["ms"] for r in rows])) if n else 0,
        "calls_per_click": round(statistics.mean([r["calls"] for r in rows]), 2) if n else 0,
        "rows": rows,
    }
    print(f"\n===== SUMMARY (verify_click_point={summary['verify_click_point']}) =====")
    for k in ("n", "mis_localization_rate", "catch_rate", "false_refusal_rate",
              "wrong_click_rate", "latency_ms_mean", "calls_per_click"):
        print(f"  {k:22s} {summary[k]}")
    print(f"  (mis-localized={len(mis)}  wrong-clicks={len(wrong_clicks)}  "
          f"false-refusals={len(false_refusals)})")
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
