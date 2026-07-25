"""Slice 38 Stage 4 vision harness — the three COMMIT confirms now show WHAT,
not just WHERE.

Before this slice each of these named the action and hid its payload, so you
approved a keystroke without seeing the command it submits:

    Type into 'Windows PowerShell'
    Press enter (submit) in 'Windows PowerShell'
    browse_key("Enter")                      <- not gated at all

NOT collected by pytest (harness_ prefix). Needs the server running:

    python run.py --no-open
    python tests/harness_commit_modal.py <output_dir>

Checks (DOM-asserted, exit 1 on failure):
  type_box_is_verbatim_text          — box === classify_type's command, byte-equal
  type_payload_visible_on_screen     — the dangerous string is actually rendered
  press_box_shows_recorded_payload   — the typed command + its age label
  webkey_box_shows_focused_value     — real-mode Enter names the site + payload
  modal_and_mono_box_visible_prewrap — for each of the three
Screenshots: hud38_{type,press,webkey}_modal.png — INSPECT THEM. DOM checks
prove structure, not that a human can read it.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from jarvis.core.settings_store import settings
from jarvis.primitives import input as jinput
from jarvis.primitives import web

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
FAILURES: list[str] = []

TERMINAL = "Windows PowerShell"
SHELL_CMD = "del /s /q C:\\Users\\malek\\Documents\\*"
WEB_PAYLOAD = "transfer $5000 to account 9912"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def show(page, info: dict, shot: str) -> str:
    """Push a real classifier result into the HUD's confirm modal, screenshot."""
    page.evaluate(
        """([desc, cmd]) => window.__hudEvent({
             type: 'confirm_request', id: 'slice38-' + Math.random(),
             description: desc, command: cmd, timeout_s: 300})""",
        [info["description"], info.get("command", "")])
    page.wait_for_timeout(300)
    page.screenshot(path=f"{OUT}/{shot}")
    return page.evaluate(
        "() => document.getElementById('confirm-command').textContent")


def visible(page) -> bool:
    return page.evaluate("""() => {
      const box = document.getElementById('confirm-command');
      const back = document.getElementById('confirm-backdrop');
      const s = getComputedStyle(box);
      return !back.classList.contains('hidden') && s.display !== 'none'
             && s.whiteSpace.startsWith('pre') && s.fontFamily.length > 0;
    }""")


def main() -> int:
    # Pin the window resolution so the harness never touches a real window.
    jinput._target_window = lambda w=None: (None, TERMINAL)
    jinput._last_typed.clear()

    orig_mode = settings.get("web.profile_mode", "isolated")
    orig_actions = settings.get("web.allow_actions", False)
    orig_focused = web.session.focused_field
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 860})
            page.goto("http://127.0.0.1:8000", wait_until="networkidle")
            page.wait_for_function("typeof window.__hudEvent === 'function'")

            # ---- 1. type_text into a terminal -----------------------------
            info = jinput.classify_type({"text": SHELL_CMD, "window": "pwsh"})
            check("type_tier_is_confirm", info["tier"] == "confirm", str(info["tier"]))
            got = show(page, info, "hud38_type_modal.png")
            check("type_box_is_verbatim_text", got == info["command"],
                  f"got={got!r}")
            check("type_payload_visible_on_screen", SHELL_CMD in got)
            check("modal_and_mono_box_visible_prewrap/type", visible(page))

            # ---- 2. press_keys enter, after JARVIS typed ------------------
            jinput._record_typed(TERMINAL, SHELL_CMD)
            info = jinput.classify_press({"combo": "enter", "window": "pwsh"})
            check("press_tier_is_confirm", info["tier"] == "confirm")
            got = show(page, info, "hud38_press_modal.png")
            check("press_box_shows_recorded_payload",
                  SHELL_CMD in got and "JARVIS typed this" in got, f"got={got!r}")
            check("modal_and_mono_box_visible_prewrap/press", visible(page))

            # ---- 3. browse_key Enter in REAL mode -------------------------
            settings.set("web.profile_mode", "real", persist=False)
            settings.set("web.allow_actions", True, persist=False)
            web.session.current_url = "https://bank.example.com/transfer"
            web.session.focused_field = lambda: {
                "found": True, "isPassword": False, "value": WEB_PAYLOAD}
            info = web.classify_web_key({"key": "enter"})
            check("webkey_tier_is_confirm", info["tier"] == "confirm",
                  str(info["tier"]))
            check("webkey_description_names_the_site",
                  "bank.example.com" in info["description"], info["description"])
            got = show(page, info, "hud38_webkey_modal.png")
            check("webkey_box_shows_focused_value", got == WEB_PAYLOAD, f"got={got!r}")
            check("modal_and_mono_box_visible_prewrap/webkey", visible(page))

            browser.close()
    finally:
        web.session.focused_field = orig_focused
        web.session.current_url = None
        settings.set("web.profile_mode", orig_mode, persist=False)
        settings.set("web.allow_actions", orig_actions, persist=False)
        jinput._last_typed.clear()

    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
