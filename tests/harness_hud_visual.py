"""HUD visual harness (slice 7) — drives the real HUD page with scripted
events via the window.__hudEvent test hook, asserts the Action Log /
telemetry DOM, and captures screenshots for human inspection.

NOT collected by pytest (harness_ prefix). Needs the server running:

    python run.py --no-open
    python tests/harness_hud_visual.py <output_dir>

Checks (DOM-asserted, exit 1 on failure):
  rows_match_scripted_chain   — order, statuses, args, notes
  end_event_unknown_n_upserts — mid-chain connect can't crash the renderer
  row_cap_80                  — log never grows unbounded
  snapshot_hydrates_panel     — the WS 'chain' snapshot rebuilds the panel
  telemetry_dom_values        — synthetic telemetry renders (incl. GPU "—")
Screenshots: hud7_midchain.png, hud7_endstates.png, hud7_telemetry.png,
hud7_narrow.png — inspect them; DOM checks don't prove looks.
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def rows(page):
    return page.eval_on_selector_all(
        "#action-log .log-row",
        "els => els.map(e => [e.dataset.status, e.querySelector('.log-tool')?.textContent,"
        " e.querySelector('.log-args')?.textContent, e.querySelector('.log-note')?.textContent])")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 860})
        page.goto("http://127.0.0.1:8000", wait_until="networkidle")
        page.wait_for_function("typeof window.__hudEvent === 'function'")

        # ---- scripted chain: plan -> ok step -> failed step -> revision ----
        page.evaluate("""() => {
          const E = window.__hudEvent;
          E({type:'plan', revision:1, steps:['open spotify','find playlist','play it']});
          E({type:'step', n:1, step:1, total:3, tool:'launch_app', args:'name=spotify', status:'start'});
          E({type:'step', n:1, step:2, total:3, tool:'launch_app', args:'name=spotify', status:'ok',
             note:'Launched Spotify.exe (pid 4242). VERIFY [VERIFIED]'});
          E({type:'step', n:2, step:2, total:3, tool:'click', args:'target=Search', status:'start'});
          E({type:'step', n:2, step:2, total:3, tool:'click', args:'target=Search', status:'failed',
             note:'FAILED: ambiguous'});
          E({type:'plan', revision:2, steps:['open spotify','search instead','play it']});
          E({type:'step', n:3, step:2, total:3, tool:'click', args:'target=Search box', status:'start'});
          window.__hudSetState('executing', '2/3 · click');
        }""")
        page.wait_for_timeout(350)
        page.screenshot(path=f"{OUT}/hud7_midchain.png")
        r = rows(page)
        headers = page.eval_on_selector_all(
            "#action-log .log-plan", "els => els.map(e => e.textContent)")
        check("rows_match_scripted_chain",
              len(r) == 3
              and r[0][0] == "ok" and "launch_app" in r[0][1] and "VERIFIED" in (r[0][3] or "")
              and r[1][0] == "failed" and "ambiguous" in (r[1][3] or "")
              and r[2][0] == "running" and "Search box" in r[2][2]
              and len(headers) == 2 and "REV 2" in headers[1].upper(),
              f"rows={r} headers={headers}")

        # ---- cancelled end + chain_end restyles the running row ----
        page.evaluate("""() => {
          const E = window.__hudEvent;
          E({type:'step', n:3, step:2, total:3, tool:'click', args:'target=Search box',
             status:'cancelled', note:'CANCELLED (the user declined)'});
          E({type:'chain_end', status:'cancelled'});
          window.__hudSetState('idle', '');
        }""")
        page.wait_for_timeout(250)
        page.screenshot(path=f"{OUT}/hud7_endstates.png")
        r = rows(page)
        check("cancelled_restyles_row", r[2][0] == "cancelled", str(r[2]))

        # ---- unknown-n end event upserts, never crashes ----
        page.evaluate("""() => {
          window.__hudEvent({type:'step', n:99, step:1, total:2, tool:'type_text',
                             args:'text=hello', status:'ok', note:'VERIFY: confirmed'});
        }""")
        r = rows(page)
        check("end_event_unknown_n_upserts",
              len(r) == 4 and r[3][0] == "ok" and "type_text" in r[3][1], str(r[-1:]))

        # ---- cap at 80 rows ----
        page.evaluate("""() => {
          for (let i = 100; i < 200; i++)
            window.__hudEvent({type:'step', n:i, step:1, total:1, tool:'click',
                               args:'target=item'+i, status:'ok'});
        }""")
        n_rows = page.eval_on_selector_all("#action-log .log-row", "els => els.length")
        last = rows(page)[-1]
        check("row_cap_80", n_rows == 80 and "item199" in last[2], f"n={n_rows} last={last}")

        # ---- snapshot hydration (fresh page state via reload) ----
        page.evaluate("""() => {
          window.__hudEvent({type:'chain', steps:['a','b'], revision:1, cursor:1,
            calls:[{n:1, tool:'launch_app', status:'ok', args:'name=notepad', note:'VERIFY ok'},
                   {n:2, tool:'click', status:'start', args:'target=File'}],
            aborted:null});
        }""")
        r = rows(page)
        check("snapshot_hydrates_panel",
              "launch_app" in r[-2][1] and r[-1][0] == "running" and "File" in r[-1][2],
              str(r[-2:]))

        # ---- telemetry rendering (synthetic; GPU absent -> "—") ----
        page.evaluate("""() => {
          window.__hudEvent({type:'telemetry', cpu:37.5, ram:66.8,
                             ram_used_gb:10.1, ram_total_gb:15.4,
                             window:'IconPad — <script>alert(1)</script>'});
        }""")
        page.wait_for_timeout(150)
        page.screenshot(path=f"{OUT}/hud7_telemetry.png")
        telem = page.evaluate("""() => ({
          cpu: document.getElementById('tel-cpu').textContent,
          ram: document.getElementById('tel-ram').textContent,
          gpu: document.getElementById('tel-gpu').textContent,
          win: document.getElementById('tel-window').textContent,
          clock: document.getElementById('tel-clock').textContent,
          alerted: window.__alerted === true,
        })""")
        check("telemetry_dom_values",
              "38" in telem["cpu"] or "37" in telem["cpu"],
              str(telem))
        check("telemetry_gpu_dash_when_absent", "—" in telem["gpu"], telem["gpu"])
        check("window_title_not_injected",
              "<script>" in telem["win"] and not telem["alerted"], telem["win"])
        check("clock_ticking", ":" in telem["clock"], telem["clock"])

        # ---- narrow viewport hides panels ----
        page.set_viewport_size({"width": 900, "height": 800})
        page.wait_for_timeout(150)
        page.screenshot(path=f"{OUT}/hud7_narrow.png")
        hidden = page.evaluate("""() => {
          const log = getComputedStyle(document.getElementById('action-log-panel')).display;
          const tel = getComputedStyle(document.getElementById('telemetry-panel')).display;
          return log === 'none' && tel === 'none';
        }""")
        check("narrow_hides_panels", hidden)

        browser.close()

    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
