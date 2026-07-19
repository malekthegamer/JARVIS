"""Audit-viewer visual harness (slice 28). NOT pytest-collected.

Start the server pointed at a scratch audit log, then drive this:

    $env:JARVIS_AUDIT_FILE = "<scratch>/seed_audit.jsonl"
    python run.py --no-open
    $env:JARVIS_AUDIT_FILE = "<scratch>/seed_audit.jsonl"
    python tests/harness_audit_visual.py <output_dir>

This harness SEEDS that same file with varied records (auto/confirm/blocked;
ok/failed/cancelled; a dry-run; an enc-null 'no data' row), then DOM-asserts
the /audit page renders them, reveals a record (proving decrypt-on-reveal),
and screenshots for the §4 vision check (Read the PNG). Never touches the real
data/audit/ log — everything lives in JARVIS_AUDIT_FILE.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root for `jarvis`

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
FAILURES: list[str] = []
SHELL_ARG = "echo AUDIT-REVEAL-MARKER-42"   # distinctive, no backslashes to JSON-escape


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def seed() -> None:
    path = os.environ.get("JARVIS_AUDIT_FILE")
    if not path:
        print("FAIL  JARVIS_AUDIT_FILE not set — cannot seed"); sys.exit(2)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.unlink()
    from jarvis.core.audit import AuditLog
    log = AuditLog(p)
    log.record(tool="launch_app", tier="auto", status="ok",
               args={"name": "notepad"}, result="OK: launched.")
    log.record(tool="run_shell", tier="confirm", status="ok", gate="approved",
               args={"command": SHELL_ARG}, result="OK: exit 0.")
    log.record(tool="delete_file", tier="confirm", status="cancelled",
               gate="declined", args={"name": "secret.txt"},
               result="CANCELLED (the user declined).")
    log.record(tool="run_shell", tier="blocked", status="failed",
               args={"command": "format C: /y"},
               result="BLOCKED: disk_format_or_wipe.")
    log.record(tool="set_volume", tier="auto", status="ok", dry_run=True,
               args={"level": 30}, result="DRY RUN (not executed).")
    # a hand-written enc-null line: the honest 'no data' (encryption unavailable) row
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": "2026-07-19T04:00:00.000000+00:00", "chain": None,
            "tool": "send_email", "tier": "confirm", "gate": "approved",
            "status": "ok", "dry_run": False, "enc": None,
            "payload_error": "encryption unavailable"}) + "\n")
    print(f"seeded 6 records -> {p}")


def main() -> int:
    seed()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 1000})
        page.goto("http://127.0.0.1:8000/audit", wait_until="networkidle")
        page.wait_for_selector(".audit-row")
        page.wait_for_timeout(400)

        rows = page.eval_on_selector_all(".audit-row", "els => els.length")
        check("rows_rendered", rows >= 6, f"{rows} rows")

        for tier in ("tier-auto", "tier-confirm", "tier-blocked"):
            n = page.eval_on_selector_all(f".badge.{tier}", "els => els.length")
            check(f"tier_badge:{tier}", n >= 1, f"{n}")
        for st in ("st-ok", "st-failed", "st-cancelled"):
            n = page.eval_on_selector_all(f".c-status.{st}", "els => els.length")
            check(f"status_class:{st}", n >= 1, f"{n}")
        check("dry_marker", page.eval_on_selector_all(".c-tool .dry", "e => e.length") >= 1)
        # enc-null row shows an honest 'no data' cell (no reveal button)
        nodata = page.eval_on_selector_all(
            ".c-rev", "els => els.filter(e => /no data/.test(e.textContent)).length")
        check("enc_null_no_data", nodata >= 1, f"{nodata}")

        # Reveal the run_shell/confirm record -> its payload box must show the arg.
        # It's the 2nd-oldest seed, so 2nd from the bottom (newest-first order).
        btn = page.query_selector(
            "xpath=//li[contains(@class,'audit-row')][.//span[contains(@class,'tier-confirm')]"
            "][.//span[text()='run_shell']]//button[contains(@class,'reveal-btn')]")
        check("reveal_button_found", btn is not None)
        if btn:
            btn.click()
            page.wait_for_selector(".payload")
            page.wait_for_timeout(300)
            ptext = page.text_content(".payload") or ""
            check("reveal_shows_decrypted_arg", SHELL_ARG in ptext, ptext[:120])

        page.screenshot(path=f"{OUT}/audit_full.png", full_page=True)
        print(f"screenshot -> {OUT}/audit_full.png")
        browser.close()
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
