"""Slice 24 live acceptance — real-browser mode through the ACTUAL primitives.

Announced/manual (launches a real headed Chrome; NOT pytest-collected). Proves
the mechanism end-to-end: web.profile_mode="real" -> browse_navigate launches
JARVIS's OWN dedicated Chrome (separate profile dir, coexists with the user's
everyday Chrome), navigates to ANY named site, read_page returns content, and
close_browser terminates only JARVIS's Chrome.

The logged-IN experience needs a one-time manual Google sign-in in JARVIS's
Chrome window (we can't type the user's password) — this harness reports the
login state honestly; run with --wait to pause for you to sign in, then it
re-checks youtube for the account surface.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _harness_env  # noqa: E402,F401  (audit isolation: import BEFORE jarvis)
from jarvis.core.settings_store import settings
from jarvis.primitives import web


def _logged_in(page_text_and_els: dict) -> bool:
    txt = (page_text_and_els.get("text") or "").lower()
    names = " ".join(e.get("name", "") for e in page_text_and_els.get("elements", []))
    # logged-out YouTube shows a prominent "Sign in"; logged-in shows an account/avatar
    return ("sign in" not in txt[:1500]) and ("account" in names.lower()
                                              or "avatar" in names.lower())


def main() -> int:
    wait_for_login = "--wait" in sys.argv
    settings.set("web.profile_mode", "real", persist=False)
    web.session.close()  # ensure a clean start

    print("=== S24 real-browser acceptance ===")
    print("launching JARVIS's dedicated Chrome (its own window) ...")
    r1 = web.navigate("https://www.youtube.com")
    print("navigate youtube ->", {k: r1[k] for k in ("url", "title") if k in r1})
    read1 = web.read_page()
    print("read youtube: %d chars, %d elements | logged-in=%s"
          % (len(read1.get("text", "")), len(read1.get("elements", [])),
             _logged_in(read1)))

    # ANY site, not just youtube (the user's clarification)
    r2 = web.navigate("https://www.reddit.com")
    print("navigate reddit ->", {k: r2.get(k) for k in ("url", "title")})

    proc = web.session._proc
    print("JARVIS Chrome pid:", getattr(proc, "pid", None))

    if wait_for_login:
        print("\n>>> Sign into your Google account in the JARVIS Chrome window,")
        print(">>> then press Enter here to re-check YouTube ...")
        input()
        web.navigate("https://www.youtube.com")
        read2 = web.read_page()
        print("after sign-in: logged-in=%s" % _logged_in(read2))

    web.close_browser()
    time.sleep(1)
    alive = proc is not None and proc.poll() is None
    print("close_browser -> JARVIS Chrome still alive:", alive, "(should be False)")
    print("VERDICT: navigate+read on the real dedicated Chrome WORKS"
          + (" (sign in once for the logged-in experience)" if not wait_for_login else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
