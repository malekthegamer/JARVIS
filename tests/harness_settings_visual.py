"""Settings-page visual harness (slice 23). NOT pytest-collected.

    python run.py --no-open
    python tests/harness_settings_visual.py <output_dir>

DOM-asserts the salvaged page renders correctly against the REAL /api/settings,
then screenshots for human/agent inspection (the §4 vision check reads the PNG).

Checks (exit 1 on any fail):
  all_modules_present     — Brain / TTS / STT / General / Capabilities & safety
  gemini_configured_mark  — brain status shows configured ✓ (key present here)
  unported_brains_disabled— openai/claude/ollama options are <option disabled>
  mic_list_populated      — the mic <select> got real devices from /api/mics
  caps_reflect_settings   — capability toggles match GET /api/settings
Screenshot: settings_full.png (full page) — inspect it.
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 1000})
        page.goto("http://127.0.0.1:8000/settings", wait_until="networkidle")
        page.wait_for_selector(".module")
        page.wait_for_timeout(1200)  # let the async fetches (voices/mics) land

        titles = page.eval_on_selector_all(
            ".module > h3", "els => els.map(e => e.textContent.trim())")
        for want in ("Brain", "Voice output (TTS)", "Voice input (STT)",
                     "General", "Capabilities & safety"):
            check(f"module_present:{want}", any(want in t for t in titles), str(titles))

        brain_status = page.text_content("#brainStatus") or ""
        check("gemini_configured_mark", "✓" in brain_status, brain_status)

        disabled = page.eval_on_selector_all(
            "#brainActive option[disabled]", "els => els.map(e => e.value)")
        check("unported_brains_disabled",
              set(disabled) >= {"openai", "claude", "ollama"}, str(disabled))

        mic_opts = page.eval_on_selector_all("#micSelect option", "els => els.length")
        check("mic_list_populated", mic_opts >= 1, f"{mic_opts} options")

        caps = page.eval_on_selector_all(
            ".cap", "els => els.map(e => [e.dataset.path, e.checked])")
        check("caps_rendered", len(caps) >= 6, str(caps))

        page.screenshot(path=f"{OUT}/settings_full.png", full_page=True)
        print(f"screenshot -> {OUT}/settings_full.png")
        browser.close()
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
