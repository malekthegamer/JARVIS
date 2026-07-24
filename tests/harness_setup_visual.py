"""First-run setup panel visual harness (slice 37).

The panel is what a friend sees the very first time they launch JARVIS, so
"it renders" is not enough — it has to READ as guidance, not as an error.
DOM assertions prove structure; the screenshot is there to be looked at.

NOT collected by pytest (harness_ prefix). Needs the server running:

    python run.py --no-open
    python tests/harness_setup_visual.py <output_dir>

Checks (exit 1 on failure):
  panel_shown_when_no_key      — the whole point
  panel_hidden_when_key_set    — must never nag a configured install
  key_input_is_masked          — type=password, so a shoulder-surfer/screenshot
                                 can't lift the key
  link_opens_aistudio          — the "where do I get one" escape hatch
  error_shown_on_empty_save    — pressing Save with nothing is explained
Screenshot: setup37_panel.png — inspect it against the stated visual goal.
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
URL = "http://127.0.0.1:8000"
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def hidden(page) -> bool:
    return page.eval_on_selector("#setup-backdrop",
                                 "e => e.classList.contains('hidden')")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        # --- no key: the panel must appear -------------------------------
        page.route("**/api/setup_state", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"brain_key": false, "model_ready": true}'))
        page.goto(URL, wait_until="networkidle")
        page.wait_for_timeout(700)
        check("panel_shown_when_no_key", not hidden(page))

        check("key_input_is_masked",
              page.get_attribute("#setup-key", "type") == "password")
        href = page.get_attribute(".setup-steps a", "href") or ""
        check("link_opens_aistudio", "aistudio.google.com/apikey" in href, href)

        title = page.text_content("#setup-title") or ""
        desc = page.text_content(".setup-desc") or ""
        print(f"      title: {title.strip()!r}")
        print(f"      desc : {desc.strip()[:90]!r}...")

        page.screenshot(path=f"{OUT}/setup37_panel.png")

        # --- empty save must explain itself ------------------------------
        page.click("#setup-save")
        page.wait_for_timeout(300)
        err_hidden = page.eval_on_selector("#setup-error",
                                           "e => e.classList.contains('hidden')")
        check("error_shown_on_empty_save", not err_hidden,
              page.text_content("#setup-error") or "")

        # --- key present: the panel must stay away ------------------------
        page.unroute("**/api/setup_state")
        page.route("**/api/setup_state", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"brain_key": true, "model_ready": true}'))
        page.goto(URL, wait_until="networkidle")
        page.wait_for_timeout(700)
        check("panel_hidden_when_key_set", hidden(page))

        browser.close()

    print()
    if FAILURES:
        print(f"FAILED: {', '.join(FAILURES)}")
        return 1
    print(f"All checks passed. Screenshot in {OUT} — now LOOK at it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
