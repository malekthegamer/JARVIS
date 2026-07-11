"""Slice 11 Stage 3 vision harness — the email CONFIRM modal renders the
verbatim multi-line block (To / Subject / exact attachment path / full body)
in the monospace box, and a long body scrolls INSIDE the box (never
truncated, never blowing out the modal).

NOT collected by pytest (harness_ prefix). Needs the server running:

    python run.py --no-open
    python tests/harness_email_modal.py <output_dir>

Checks (DOM-asserted, exit 1 on failure):
  box_text_is_verbatim_block        — textContent === classify's block, byte-equal
  modal_and_mono_box_visible_prewrap
  block_renders_multiline           — newlines actually render as lines
  long_body_scrolls_not_truncated   — scrollHeight > clientHeight, last line present
Screenshots: hud11_email_modal.png, hud11_email_modal_long.png — inspect them;
DOM checks don't prove looks.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from jarvis.primitives import email as jemail
from jarvis.primitives import files

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    files.AGENT_FILES_DIR.mkdir(parents=True, exist_ok=True)
    invoice = files.AGENT_FILES_DIR / "invoice-vision-check.pdf"
    invoice.write_bytes(b"%PDF-1.4 vision-check invoice bytes")
    try:
        body = ("Hi Sam,\n\nplease find yesterday's invoice attached.\n\n"
                "Best,\nMalek (sent by JARVIS)")
        info = jemail.classify_send_email({
            "to": "sam@example.com",
            "subject": "Invoice from yesterday",
            "body": body,
            "attachment": "invoice-vision-check.pdf",
        })
        assert info["tier"] == "confirm", info
        block = info["command"]
        desc = info["description"]

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 860})
            page.goto("http://127.0.0.1:8000", wait_until="networkidle")
            page.wait_for_function("typeof window.__hudEvent === 'function'")

            page.evaluate(
                """([desc, block]) => window.__hudEvent({
                     type: 'confirm_request', id: 'vision-check-1',
                     description: desc, command: block, timeout_s: 300})""",
                [desc, block])
            page.wait_for_timeout(300)
            page.screenshot(path=f"{OUT}/hud11_email_modal.png")

            got = page.evaluate(
                "() => document.getElementById('confirm-command').textContent")
            check("box_text_is_verbatim_block", got == block,
                  f"len got={len(got)} want={len(block)}")

            visible = page.evaluate("""() => {
              const box = document.getElementById('confirm-command');
              const back = document.getElementById('confirm-backdrop');
              const s = getComputedStyle(box);
              return !back.classList.contains('hidden')
                     && s.display !== 'none'
                     && s.whiteSpace.startsWith('pre')
                     && s.fontFamily.length > 0;
            }""")
            check("modal_and_mono_box_visible_prewrap", visible)

            lines = page.evaluate("""() => {
              const box = document.getElementById('confirm-command');
              const r = document.createRange();
              r.selectNodeContents(box);
              const h = r.getBoundingClientRect().height;
              const lh = parseFloat(getComputedStyle(box).lineHeight);
              return Math.round(h / lh);
            }""")
            check("block_renders_multiline", lines >= 8, f"rendered lines={lines}")

            # long body must SCROLL inside the box, never truncate
            long_body = "\n".join(f"body line {i}" for i in range(200))
            info2 = jemail.classify_send_email({
                "to": "sam@example.com", "subject": "long",
                "body": long_body})
            page.evaluate(
                """([desc, block]) => window.__hudEvent({
                     type: 'confirm_request', id: 'vision-check-2',
                     description: desc, command: block, timeout_s: 300})""",
                [info2["description"], info2["command"]])
            page.wait_for_timeout(200)
            page.screenshot(path=f"{OUT}/hud11_email_modal_long.png")
            scroll = page.evaluate("""() => {
              const box = document.getElementById('confirm-command');
              return {sh: box.scrollHeight, ch: box.clientHeight,
                      full: box.textContent.includes('body line 199')};
            }""")
            check("long_body_scrolls_not_truncated",
                  scroll["sh"] > scroll["ch"] and scroll["full"], str(scroll))

            browser.close()
    finally:
        invoice.unlink(missing_ok=True)

    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
