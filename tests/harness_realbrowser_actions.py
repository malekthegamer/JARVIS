"""Slice 25 live acceptance — JARVIS ACTS in the real logged-in browser.

Announced/manual (real Chrome, real accounts, real Gemini; NOT pytest-collected).
Proves the two user examples end-to-end through the REAL brain:
  1) "search for MrBeast on YouTube and open a random video" -> a /watch page
  2) "open Claude and type a prompt about X"                 -> text in the box
Requires: real mode + allow_actions on, and a one-time Google sign-in in
JARVIS's Chrome (harness_realbrowser_accept.py --wait). Mechanical checks only —
never the model's prose.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import threading

from tests import _harness_env  # noqa: E402,F401  (audit isolation: import BEFORE jarvis)
from jarvis.core.confirmations import confirmations
from jarvis.core.settings_store import settings
from jarvis.primitives import web


def _auto_approve():
    """Stand in for the user saying yes to CONFIRMs (cross-origin navigate,
    committal clicks) so the acceptance can run unattended. In real use the
    user approves these live."""
    def responder(event):
        if event.get("type") == "confirm_request":
            print(f"    [auto-approve] {event.get('description','')[:80]}")
            threading.Thread(target=lambda: (
                time.sleep(0.1), confirmations.resolve(event["id"], True))).start()
    return confirmations.subscribe(responder)


def _url() -> str:
    try:
        return web.session._do(lambda pg: pg.url)
    except Exception:
        return ""


def main() -> int:
    settings.set("web.profile_mode", "real", persist=False)
    settings.set("web.allow_actions", True, persist=False)
    web.session.close()
    from jarvis.brain import JarvisBrain

    _auto_approve()  # approve cross-origin nav + committal clicks (the user's yes)
    print("=== S25 live acceptance: JARVIS acts in the real browser ===")

    # 1) YouTube: search + open a random video
    b1 = JarvisBrain()
    t0 = time.time()
    reply1 = b1.think("Go to YouTube, search for MrBeast, and open one of the "
                      "videos in the results.")
    print(f"\n[YT] reply ({time.time()-t0:.0f}s): {reply1[:160]}")
    time.sleep(3)
    yt_url = _url()
    on_watch = "/watch" in yt_url
    print(f"[YT] landed on: {yt_url}")
    print(f"[YT] VERDICT: {'OPENED A VIDEO' if on_watch else 'no /watch page — inspect'}")

    # 2) Claude: type a prompt (do NOT send — typing is the ask)
    b2 = JarvisBrain()
    t0 = time.time()
    reply2 = b2.think("Open claude.ai and type this into the message box (do not "
                      "send it): Explain quantum entanglement in one sentence.")
    print(f"\n[Claude] reply ({time.time()-t0:.0f}s): {reply2[:160]}")
    time.sleep(2)
    typed = ""
    try:
        typed = web.session._do(lambda pg: pg.evaluate(
            "() => { const e=document.querySelector('[contenteditable=\"true\"],"
            "textarea,[role=textbox]'); return e ? (e.innerText||e.value||'') : ''; }"))
    except Exception as exc:
        print("[Claude] readback error:", exc)
    print(f"[Claude] input box now contains: {typed[:80]!r}")
    print(f"[Claude] VERDICT: {'TEXT ENTERED' if 'quantum' in typed.lower() else 'no text — inspect'}")

    web.close_browser()
    print("\nDone. (committal actions like Send/Post would have asked first.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
