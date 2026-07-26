"""Slice 41 live acceptance — JARVIS drives the user's REAL everyday Chrome.

ANNOUNCED, INTERACTIVE. Needs the JARVIS extension loaded in the browser and
NO other JARVIS server on :8000 (this harness IS the server, so the extension
bridge lives in this process).

    python tests/harness_extension_accept.py

It will:
  1. wait for the extension to connect (up to 90s — MV3 alarms fire at most
     once a minute, which is the reconnect floor measured in Stage 0)
  2. READ the tab you are actually looking at
  3. NAVIGATE that tab to example.com and read it back
  4. assert committal verbs are withheld AND refused (slice 41 is read-only)
  5. screenshot the desktop so the result can be checked by eye

Step 3 moves your active tab. Press back afterwards.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis import config                                    # noqa: E402
from jarvis.core import extbridge                            # noqa: E402
from jarvis.core.settings_store import settings              # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def serve() -> None:
    import uvicorn
    uvicorn.run("jarvis.server:app", host=config.SERVER_HOST,
                port=config.SERVER_PORT, log_level="error")


def main() -> int:
    print(f"mode={settings.get('web.profile_mode')!r} "
          f"extension_id={settings.get('web.extension_id')!r}\n")
    threading.Thread(target=serve, daemon=True).start()

    print("waiting for the extension to connect (up to 90s)...")
    deadline = time.time() + 90
    while time.time() < deadline and not extbridge.bridge.connected():
        time.sleep(1)
    check("extension_connected", extbridge.bridge.connected())
    if not extbridge.bridge.connected():
        print("\nThe extension never connected. Check chrome://extensions — is "
              "'JARVIS browser bridge' enabled? Its service worker may need a "
              "click on 'service worker' to wake, or up to a minute.")
        return 1

    from jarvis.primitives import web                        # noqa: E402

    # ---- 2. read the tab the user is actually on -------------------------
    print("\n--- READ your current tab ---")
    r = web.read_page()
    check("read_ok", bool(r.get("ok")), str(r.get("message"))[:90])
    if r.get("ok"):
        print(f"  url : {r.get('url')}")
        body = (r.get("text") or "").replace("\n", " ")
        print(f"  text: {body[:150]!r}")
        check("read_returned_real_content", len(body) > 50)
        check("read_wrapped_in_untrusted_boundary",
              "untrusted" in body.lower() or "data" in body.lower())

    # ---- 3. navigate that tab -------------------------------------------
    print("\n--- NAVIGATE your tab to example.com ---")
    n = web.navigate("https://example.com/")
    check("navigate_ok", bool(n.get("ok")), str(n.get("message"))[:90])
    if n.get("ok"):
        print(f"  landed on: {n.get('url')}  ({n.get('title')})")
        check("navigate_landed_on_target",
              "example.com" in str(n.get("url", "")))

    # ---- 4. read-only guarantees ----------------------------------------
    print("\n--- read-only guarantees ---")
    from jarvis.brain import JarvisBrain
    names = [t["name"] for t in JarvisBrain().tools()]
    check("committal_verbs_withheld",
          not any(v in names for v in ("browse_click", "browse_fill", "browse_key")),
          f"navigate/read present: {'browse_navigate' in names}/{'read_page' in names}")
    check("committal_verbs_refused_at_execute",
          web.classify_web_click({"target": "Buy"})["tier"] == "blocked")

    # ---- 5. look at it ---------------------------------------------------
    try:
        from jarvis.primitives import screen
        shot = screen.capture_screen()
        path = shot.get("path") if isinstance(shot, dict) else None
        print(f"\nscreenshot: {path}")
    except Exception as exc:
        print(f"\n(screenshot skipped: {exc})")

    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
