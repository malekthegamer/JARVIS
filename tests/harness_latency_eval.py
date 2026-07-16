"""Slice 20 — PC-control latency PROFILE (measurement only, no product changes).

Where does the wall-clock actually go in a real multi-step interaction?
Monkeypatch-wraps timing around existing seams (all restored in finally),
drives real chains, prints an attribution table. NOT pytest-collected.

    python tests/harness_latency_eval.py --dry          # structural check only
    python tests/harness_latency_eval.py --reps 3       # the measured run
    python tests/harness_latency_eval.py --skip-vision  # S1 + S3 only

Categories (gate wait is measured but EXCLUDED from system latency — that's
the user's own response time; the harness auto-approves in ~0.05 s):
  model          brain provider.generate, one per tool round
  execute        primitives.execute per tool (sub-spans nested inside)
    resolve / focus / capture / diff / uia / vision_locate / vision_verify /
    audit / gate
  residual       think() wall - model - execute - gate  (orchestration)
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# --------------------------------------------------------------- tracer

class Tracer:
    """Records (t0, dt, category, label) spans via wrapped callables.
    Every wrap is undone in restore() — the process exits clean."""

    def __init__(self) -> None:
        self.events: list[tuple[float, float, str, str]] = []
        self._undo: list = []
        self._lock = threading.Lock()

    def _record(self, t0: float, dt: float, cat: str, label: str) -> None:
        with self._lock:
            self.events.append((t0, dt, cat, label))

    def wrap(self, obj, attr: str, cat: str, label=None):
        orig = getattr(obj, attr)

        def timed(*a, **k):
            t0 = time.perf_counter()
            try:
                return orig(*a, **k)
            finally:
                name = label(*a, **k) if callable(label) else (label or attr)
                self._record(t0, time.perf_counter() - t0, cat, name)

        setattr(obj, attr, timed)
        self._undo.append((obj, attr, orig))
        return orig

    def restore(self) -> None:
        for obj, attr, orig in reversed(self._undo):
            setattr(obj, attr, orig)
        self._undo.clear()

    def clear(self) -> None:
        with self._lock:
            self.events.clear()


def instrument(tracer: Tracer) -> None:
    """Wrap every seam from the plan. Module-attribute wraps intercept the
    product's own late-bound lookups; no product code is modified."""
    from jarvis.brain import jarvis_brain
    from jarvis import primitives
    from jarvis.core import audit
    from jarvis.core.confirmations import confirmations
    from jarvis.primitives import input as jinput, screen, ui_tree, vision

    provider = jarvis_brain.provider()
    tracer.wrap(provider, "generate", "model", "round")
    tracer.wrap(primitives, "execute", "execute",
                lambda name, args=None, **k: name)
    tracer.wrap(jinput, "resolve_target", "resolve", "resolve_target")
    tracer.wrap(jinput, "_target_window", "win_resolve", "_target_window")
    tracer.wrap(jinput, "read_back_text", "readback", "read_back_text")
    tracer.wrap(jinput, "_acquire_focus", "focus", "_acquire_focus")
    tracer.wrap(screen, "capture_screen", "capture", "capture_screen")
    tracer.wrap(screen, "screenshot_diff", "diff", "screenshot_diff")
    # Wrap nested UIA fns too; the report keeps only TOP-LEVEL spans, so
    # window_present>list_windows and _target_window>find_window_title never
    # double-count. (Earlier version summed all -> a -2.5s "other" artifact.)
    for fn in ("read_ui_tree", "list_windows", "window_present",
               "window_present_for_process"):
        tracer.wrap(ui_tree, fn, "uia", fn)
    tracer.wrap(vision, "locate_and_classify", "vision_locate", "locate")
    tracer.wrap(vision, "verify_point", "vision_verify", "verify_point")
    tracer.wrap(audit.audit_log, "record", "audit", "record")
    tracer.wrap(confirmations, "request", "gate", "confirm_wait")


# --------------------------------------------------------------- helpers

def auto_approve():
    """Approve every CONFIRM ~0.05 s after it appears (test_shell pattern);
    the gate span is recorded and excluded, so user wait ~= 0 here."""
    from jarvis.core.confirmations import confirmations

    def responder(event):
        if event.get("type") == "confirm_request":
            threading.Thread(target=lambda: (
                time.sleep(0.05),
                confirmations.resolve(event["id"], True))).start()
    return confirmations.subscribe(responder)


def kill_notepads() -> None:
    import psutil
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower().startswith("notepad"):
                p.kill()
        except Exception:
            pass
    time.sleep(0.5)


# --------------------------------------------------------------- reporting

def _within(a, b) -> bool:
    return a is not b and a[0] >= b[0] - 1e-9 and a[0] + a[1] <= b[0] + b[1] + 1e-6


def _top_level_subspans(events, span):
    """The maximal non-overlapping sub-spans of `span` — nested calls
    (list_windows inside window_present, find_window_title inside
    _target_window, _acquire_focus inside resolve_target) are dropped, so
    each category is counted once. Longer-first at equal start keeps the
    outer span over its child."""
    inner = sorted((e for e in events if _within(e, span)),
                   key=lambda e: (e[0], -e[1]))
    kept = []
    for e in inner:
        if not any(_within(e, k) or e is k for k in kept):
            kept.append(e)
    return kept


def report(tracer: Tracer, wall_s: float, title: str) -> dict:
    ev = list(tracer.events)
    exec_spans = [e for e in ev if e[2] == "execute"]
    model_spans = [e for e in ev if e[2] == "model"]
    model_s = sum(e[1] for e in model_spans)
    exec_s = sum(e[1] for e in exec_spans)

    # Partition each execute span into top-level sub-spans -> per-category time.
    cat_tot: dict[str, float] = {}
    cat_cnt: dict[str, int] = {}
    gate_s = 0.0
    other_s = 0.0  # unwrapped remainder inside tools (real typing, fixed sleeps)
    per_tool = []
    for span in exec_spans:
        subs: dict[str, float] = {}
        for s in _top_level_subspans(ev, span):
            subs[s[2]] = subs.get(s[2], 0.0) + s[1]
        gate_in = subs.pop("gate", 0.0)
        gate_s += gate_in
        acct = sum(subs.values())
        other = span[1] - gate_in - acct
        other_s += other
        for c, v in subs.items():
            cat_tot[c] = cat_tot.get(c, 0.0) + v
            cat_cnt[c] = cat_cnt.get(c, 0) + 1
        per_tool.append((span[3], span[1] - gate_in, subs, other, gate_in))

    exec_sys_s = exec_s - gate_s
    residual_s = wall_s - model_s - exec_s

    print(f"\n--- {title}: wall {wall_s:6.2f}s ---")
    print(f"{'category':<16}{'count':>6}{'total s':>9}{'% wall':>8}")
    print(f"{'model':<16}{len(model_spans):>6}{model_s:>9.2f}"
          f"{100 * model_s / wall_s:>7.1f}%")
    order = ["win_resolve", "resolve", "uia", "readback", "focus",
             "vision_locate", "vision_verify", "capture", "diff", "audit"]
    for cat in order:
        if cat not in cat_tot:
            continue
        print(f"{cat:<16}{cat_cnt[cat]:>6}{cat_tot[cat]:>9.2f}"
              f"{100 * cat_tot[cat] / wall_s:>7.1f}%")
    print(f"{'tool other/sleep':<16}{'':>6}{other_s:>9.2f}"
          f"{100 * other_s / wall_s:>7.1f}%  (typing, fixed settles, key send)")
    print(f"{'residual':<16}{'':>6}{residual_s:>9.2f}"
          f"{100 * residual_s / wall_s:>7.1f}%  (orchestration/prompt)")
    print(f"{'gate (EXCLUDED)':<16}{'':>6}{gate_s:>9.2f}"
          f"{100 * gate_s / wall_s:>7.1f}%  user response time, not system")

    print("per-tool:")
    for tool, tdt, subs, other, gate_in in per_tool:
        sub_txt = " ".join(f"{k}={v:.2f}" for k, v in
                           sorted(subs.items(), key=lambda kv: -kv[1]))
        print(f"  {tool:<14}{tdt:>7.2f}s  [{sub_txt} other={other:.2f}]"
              + (f"  +gate {gate_in:.2f}s excluded" if gate_in else ""))
    return {"wall_s": wall_s, "model_s": model_s, "exec_sys_s": exec_sys_s,
            "gate_s": gate_s, "residual_s": residual_s,
            "rounds": len(model_spans),
            "cat": cat_tot, "other_s": other_s}


# --------------------------------------------------------------- scenarios

S1_PROMPT = ("Open notepad and type hello world, then press enter to make "
             "a new line.")


def scenario_s1(tracer: Tracer, reps: int) -> list[dict]:
    from jarvis.brain import jarvis_brain
    out = []
    unsub = auto_approve()
    try:
        for i in range(reps):
            kill_notepads()
            tracer.clear()
            t0 = time.perf_counter()
            reply = jarvis_brain.think(S1_PROMPT)
            wall = time.perf_counter() - t0
            tools = [label for _t, _d, cat, label in tracer.events
                     if cat == "execute"]
            print(f"\n[S1 rep {i + 1}] tools={tools}")
            print(f"[S1 rep {i + 1}] reply: {reply[:100]}")
            out.append(report(tracer, wall, f"S1 rep {i + 1} (Notepad chain)"))
            kill_notepads()
    finally:
        unsub()
    return out


def scenario_s2(tracer: Tracer, reps: int) -> list[dict]:
    """Vision-path click on the VisionPad canvas (no UIA names -> vision is
    forced): resolve miss -> locate (1 model call) -> gate -> verify_point
    (2nd call) -> click. Direct execute; no brain chain."""
    from jarvis import primitives
    manifest = Path(__file__).resolve().parent / "_latency_manifest.json"
    manifest.unlink(missing_ok=True)
    pad = subprocess.Popen([sys.executable,
                            str(ROOT / "tests" / "harness_visionpad.py"),
                            str(manifest)])
    out = []
    unsub = auto_approve()
    try:
        deadline = time.time() + 20
        while time.time() < deadline and not manifest.exists():
            time.sleep(0.3)
        time.sleep(1.0)
        for i in range(reps):
            tracer.clear()
            t0 = time.perf_counter()
            res = primitives.execute(
                "click", {"target": "the save icon", "window": "VisionPad"})
            wall = time.perf_counter() - t0
            print(f"\n[S2 rep {i + 1}] result: {str(res)[:90]}")
            out.append(report(tracer, wall, f"S2 rep {i + 1} (vision click)"))
    finally:
        unsub()
        pad.kill()
        manifest.unlink(missing_ok=True)
    return out


def scenario_s3() -> None:
    """TTS overhead: synthesis (network) vs playback (audio duration)."""
    from jarvis.providers.tts.edge_tts_provider import EdgeTTSProvider
    from jarvis.voice import playback
    text = ("Done, sir. I have opened Notepad, typed hello world, "
            "and added a new line as requested.")
    p = EdgeTTSProvider()
    t0 = time.perf_counter()
    data = p.synthesize(text)
    synth = time.perf_counter() - t0
    if not data:
        print("\n[S3] edge-tts synthesis returned nothing (network?) — skipped")
        return
    t0 = time.perf_counter()
    playback.play_bytes(data)
    play = time.perf_counter() - t0
    print(f"\n[S3 voice] synthesis={synth:.2f}s (network, blocks the reply)  "
          f"playback={play:.2f}s (speech duration, {len(data):,} bytes)")


# --------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--dry", action="store_true",
                    help="structural check: wrap, run one cheap AUTO tool, restore")
    ap.add_argument("--skip-vision", action="store_true")
    ap.add_argument("--skip-voice", action="store_true")
    ns = ap.parse_args()

    tracer = Tracer()
    instrument(tracer)
    try:
        if ns.dry:
            from jarvis import primitives
            t0 = time.perf_counter()
            res = primitives.execute("get_volume", {})
            report(tracer, time.perf_counter() - t0, "dry (get_volume)")
            print(f"\ndry result: {res[:70]}")
            return
        s1 = scenario_s1(tracer, ns.reps)
        s2 = [] if ns.skip_vision else scenario_s2(tracer, ns.reps)
        print("\n================= SUMMARY (medians across reps) ================")
        for name, rows in (("S1 Notepad chain", s1), ("S2 vision click", s2)):
            if not rows:
                continue
            med = {k: statistics.median(r[k] for r in rows)
                   for k in ("wall_s", "model_s", "exec_sys_s", "gate_s",
                             "residual_s", "rounds")}
            print(f"{name}: wall {med['wall_s']:.1f}s | model {med['model_s']:.1f}s "
                  f"({int(med['rounds'])} rounds) | tools(sys) {med['exec_sys_s']:.1f}s "
                  f"| residual {med['residual_s']:.1f}s | gate excluded {med['gate_s']:.2f}s")
        if not ns.skip_voice:
            scenario_s3()
    finally:
        tracer.restore()


if __name__ == "__main__":
    main()
