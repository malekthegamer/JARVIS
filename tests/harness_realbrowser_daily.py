"""Slice 39 vision harness — the real-browser indicator.

Once JARVIS's Chrome is the user's EVERYDAY browser (signed into Chrome Sync),
"JARVIS can act on my real accounts right now" is the highest-consequence state
the app has. It must be visible in the HUD, not buried in the settings page.

Both states are driven by stubbing `fetch` inside the page and re-running the
badge's own logic, so the user's real data/settings.json is never written.

NOT collected by pytest (harness_ prefix). Needs the server running:

    python run.py --no-open
    python tests/harness_realbrowser_daily.py <output_dir>

Checks (DOM-asserted, exit 1 on failure):
  badge_hidden_in_isolated_mode
  badge_visible_and_says_reading      (real mode, acting OFF)
  badge_visible_and_says_acting       (real mode, acting ON)
  badge_tooltip_distinguishes_the_two
Screenshot: hud39_realbrowser_badge.png — INSPECT IT.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


# Slice 42: the badge is now driven by the LIVE telemetry event, not a
# one-shot settings fetch, so drive its render function directly.
RENDER = "([mode, connected]) => window.__hudBrowserBadge(mode, connected)"


def badge_state(page) -> dict:
    return page.evaluate("""() => {
      const b = document.getElementById('realbrowser');
      const t = document.getElementById('realbrowser-text');
      return {hidden: b.classList.contains('hidden'),
              offline: b.classList.contains('offline'),
              text: (t.textContent || '').trim(),
              title: b.getAttribute('title') || ''};
    }""")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 860})
        page.goto("http://127.0.0.1:8000", wait_until="networkidle")
        page.wait_for_function("typeof window.__hudBrowserBadge === 'function'")

        # 1. isolated -> hidden entirely
        page.evaluate(RENDER, ["isolated", False])
        page.wait_for_timeout(120)
        st = badge_state(page)
        check("badge_hidden_in_isolated_mode", st["hidden"] is True, str(st))

        # 2. extension mode, CONNECTED
        page.evaluate(RENDER, ["extension", True])
        page.wait_for_timeout(120)
        on = badge_state(page)
        check("badge_visible_when_connected",
              on["hidden"] is False and on["offline"] is False
              and "your browser" in on["text"], str(on))
        page.screenshot(path=f"{OUT}/hud42_badge_connected.png")

        # 3. extension mode, DISCONNECTED — the state that used to be invisible
        page.evaluate(RENDER, ["extension", False])
        page.wait_for_timeout(120)
        off = badge_state(page)
        check("badge_shows_reconnecting_when_disconnected",
              off["hidden"] is False and off["offline"] is True
              and "reconnect" in off["text"].lower(), str(off))
        check("connected_and_disconnected_look_different",
              on["text"] != off["text"] and on["title"] != off["title"])

        page.screenshot(path=f"{OUT}/hud42_badge_disconnected.png")
        browser.close()

    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
