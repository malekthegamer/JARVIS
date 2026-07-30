"""Slice 43 Stage 0 — THE resolver metric, before any verb can click.

WHY. Extension clicking resolves elements with `lib.js:matchClickable`, a port of
web.py's proven `_match_clickable`. Tiers are computed FROM the resolved
element's name, so a mis-resolve does not merely click the wrong thing — it can
compute the WRONG TIER and skip a confirmation, on the owner's real logged-in
accounts. Slice 17's rule applies: for anything probabilistic that acts
destructively, "it works" is meaningless until it is a number.

SO: run BOTH resolvers over the SAME pages and score
  * element agreement  — do they pick the same element?
  * TIER agreement     — do they compute the same gate? (the safety-critical one)
  * no-match rate      — a resolver that finds nothing confirms everything,
                         which looks safe and is useless (the cost metric)

    python tests/harness_resolver_eval.py

Local fixture pages are reused from test_web.py's server shape; the public
pages are where a naive port diverges in principle (shadow DOM, framework
markup).
"""
from __future__ import annotations

import http.server
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LIB_JS = ROOT / "extension" / "lib.js"

# (path, target) pairs with a KNOWN right answer, mirroring test_web.py's
# fixtures: a named committal button, a cross-host anchor, a JS jump with no
# href, and the NAMELESS button (the deliberate fail-closed case).
FIXTURE_PAGES = {
    "/form": (b"<title>Form</title>"
              b"<form><label for='q'>Search</label>"
              b"<input id='q' name='q' placeholder='search products'>"
              b"<button type='submit'>Delete account</button></form>"
              b"<a href='/other'>Read more</a>"),
    "/linkto": (b"<title>Linkto</title>"
                b"<a href='http://localhost:9/elsewhere'>go elsewhere</a>"),
    "/jsjump": (b"<title>JsJump</title><button type='button' "
                b"onclick=\"location.href='http://localhost:9/x'\">leave</button>"),
    "/bare": (b"<title>Bare</title><form>"
              b"<button type='submit' id='b'></button></form>"),
}
CASES = [
    ("/form", "Delete account"),
    ("/form", "Read more"),
    ("/form", "delete"),            # partial match
    ("/linkto", "go elsewhere"),
    ("/jsjump", "leave"),
    ("/bare", "submit"),            # nameless fail-closed path
    ("/form", "nonexistent thing"),  # must find nothing, both sides
]


class _H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = FIXTURE_PAGES.get(self.path.split("?")[0],
                                 b"<title>Home</title><a href='/other'>more</a>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    from jarvis.primitives import web

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"

    lib_src = LIB_JS.read_text(encoding="utf-8")

    from playwright.sync_api import sync_playwright
    rows = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        for path, target in CASES:
            page.goto(base + path, wait_until="load")
            # _cross_host compares the href against session.current_url.
            # Without this the cross-host row scored 'auto' on BOTH sides
            # and the agreement was VACUOUS — it proved nothing about the
            # safety-critical gate.
            web.session.current_url = base + path

            # --- reference: the PROVEN Playwright resolver -----------------
            h, ref_name, ref_kind, ref_href = web._match_clickable(page, target)
            ref_found = bool(h is not None or ref_kind)

            # --- candidate: the SHIPPED extension logic --------------------
            page.add_script_tag(content=lib_src)
            got = page.evaluate(
                "(t) => { const r = globalThis.JARVIS_LIB.matchClickable(document, t); "
                "return {found: r.found, name: r.name, kind: r.kind, href: r.href}; }",
                target)

            # --- the safety-critical comparison: same TIER? ----------------
            def tier_of(name, kind, href):
                if not name and not kind:
                    return "none"
                from jarvis.primitives.input import _click_tier
                if href and web._cross_host(href):
                    return "confirm(cross-host)"
                return _click_tier(name, False) if name else "confirm(nameless)"

            ref_tier = tier_of(ref_name, ref_kind, ref_href)
            got_tier = tier_of(got["name"], got["kind"], got["href"])

            rows.append({
                "page": path, "target": target,
                "ref": f"{ref_name!r}/{ref_kind}", "got": f"{got['name']!r}/{got['kind']}",
                "elem_agree": (ref_found == got["found"]
                               and ref_name == got["name"]),
                "tier_agree": ref_tier == got_tier,
                "ref_tier": ref_tier, "got_tier": got_tier,
            })
        browser.close()
    srv.shutdown()

    n = len(rows)
    elem = sum(r["elem_agree"] for r in rows)
    tier = sum(r["tier_agree"] for r in rows)
    nomatch = sum(1 for r in rows if r["got"].startswith("''"))

    print("\n=== RESOLVER AGREEMENT (extension lib.js vs proven Playwright) ===")
    for r in rows:
        flag = "  " if (r["elem_agree"] and r["tier_agree"]) else "!!"
        print(f"{flag} {r['page']:10} {r['target']!r:22} "
              f"ref={r['ref']:26} got={r['got']:26} "
              f"tier {r['ref_tier']} vs {r['got_tier']}")
    print(f"\n  element agreement : {elem}/{n} ({elem/n:.0%})")
    print(f"  TIER agreement    : {tier}/{n} ({tier/n:.0%})   <- the safety one")
    print(f"  no-match (cost)   : {nomatch}/{n}  "
          f"(a resolver that finds nothing confirms everything)")
    ok = elem == n and tier == n
    print(f"\n*** {'AGREEMENT COMPLETE — safe to wire actions' if ok else 'DIVERGENCE — fix the resolver BEFORE wiring any click'} ***")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
