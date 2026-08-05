"""Slice 42 — THE browser-control metric: latency AND success rate.

HARNESS.md §6b: when "better" is a claim, make it a number, and build the
metric BEFORE the fix — it may tell you not to build the fix. The owner asked
for "fast and almost instantaneous"; that is a claim, so it needs a baseline.

ALWAYS REPORTS THE COST NEXT TO THE WIN. A navigate that returns in 5ms because
it stopped waiting for the page is not faster, it is broken — so success rate
is measured on the same runs, and a "win" that drops it is a regression.

ANNOUNCED, INTERACTIVE. Drives the owner's REAL browser: it navigates a tab
several times. Have a throwaway tab focused.

    python tests/harness_browser_bench.py [label]

`label` is written into the output so before/after runs are comparable.
"""
from __future__ import annotations

import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import _harness_env  # noqa: E402,F401  (audit isolation: import BEFORE jarvis)
from jarvis import config                          # noqa: E402
from jarvis.core import extbridge                  # noqa: E402
from jarvis.core.settings_store import settings    # noqa: E402

LABEL = sys.argv[1] if len(sys.argv) > 1 else "baseline"

# Fixed set so before/after are comparable. Deliberately mixed: two trivial
# pages and two heavy real ones — the heavy ones are where "wait for the load
# to fully complete" hurts, and they are what the owner actually uses.
URLS = [
    "https://example.com/",
    "https://en.wikipedia.org/wiki/Google_Chrome",
    "https://www.youtube.com/",
    "https://github.com/",
]
REPS = 2                      # 4 urls x 2 = 8 navigations


def _chrome_running() -> bool:
    """Is Chrome even open? A diagnostic that GUESSES is how v1.0.4 and v1.0.6
    both burned time — say which of the two things is actually wrong."""
    try:
        import psutil
        for p in psutil.process_iter(attrs=["name"]):
            if (p.info.get("name") or "").lower() == "chrome.exe":
                return True
    except Exception:
        pass
    return False


def serve() -> None:
    import uvicorn
    uvicorn.run("jarvis.server:app", host=config.SERVER_HOST,
                port=config.SERVER_PORT, log_level="error")


def stats(name: str, samples: list[float], ok: int, total: int) -> None:
    if samples:
        med = statistics.median(samples)
        p90 = sorted(samples)[max(0, int(len(samples) * 0.9) - 1)]
        print(f"  {name:10} median {med*1000:7.0f} ms   p90 {p90*1000:7.0f} ms"
              f"   success {ok}/{total}")
    else:
        print(f"  {name:10} no successful samples   success {ok}/{total}")


def main() -> int:
    if str(settings.get("web.profile_mode", "")).lower() != "extension":
        print("profile_mode is not 'extension' — this benchmark measures the "
              "real-browser extension path.")
        return 2

    threading.Thread(target=serve, daemon=True).start()
    print("waiting for the extension (up to 90s)...")
    deadline = time.time() + 90
    while time.time() < deadline and not extbridge.bridge.connected():
        time.sleep(1)
    if not extbridge.bridge.connected():
        if not _chrome_running():
            print("Chrome is NOT RUNNING - that is the whole problem. "
                  "Open Chrome and run this again.")
        else:
            print("Chrome is running but the extension did not connect. "
                  "chrome://extensions - is 'JARVIS browser bridge' enabled? "
                  "Reload it; it must be v1.0.1 or newer.")
        return 1

    from jarvis.primitives import web              # noqa: E402

    nav_times: list[float] = []
    read_times: list[float] = []
    nav_ok = read_ok = 0
    nav_n = read_n = 0
    failures: list[str] = []

    print(f"\n=== {LABEL} ===")
    for rep in range(REPS):
        for url in URLS:
            nav_n += 1
            t0 = time.time()
            r = web.navigate(url)
            dt = time.time() - t0
            landed = str(r.get("url") or "")
            # SUCCESS means it actually got there — not merely that the call
            # returned. This is the guard against a fake speed win.
            host = url.split("//", 1)[1].split("/", 1)[0].replace("www.", "")
            good = bool(r.get("ok")) and host in landed
            if good:
                nav_ok += 1
                nav_times.append(dt)
            else:
                failures.append(f"navigate {url} -> ok={r.get('ok')} "
                                f"landed={landed[:50]!r} {str(r.get('message'))[:60]}")
            print(f"  [{rep+1}] navigate {host:20} {dt*1000:6.0f} ms  "
                  f"{'ok' if good else 'FAIL'}")

            read_n += 1
            t0 = time.time()
            rr = web.read_page()
            dt = time.time() - t0
            # A read is only good if it returned real page text.
            body = rr.get("text") or ""
            good_read = bool(rr.get("ok")) and len(body) > 200
            if good_read:
                read_ok += 1
                read_times.append(dt)
            else:
                failures.append(f"read after {host} -> ok={rr.get('ok')} "
                                f"len={len(body)}")

    print(f"\n--- {LABEL} RESULTS ---")
    stats("navigate", nav_times, nav_ok, nav_n)
    stats("read", read_times, read_ok, read_n)
    if failures:
        print("\n  failures:")
        for f in failures[:8]:
            print(f"    {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
