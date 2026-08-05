"""Slice 43 live acceptance — JARVIS ACTS in a real Chrome via the extension.

WHY A HARNESS AND NOT PYTEST. Two of these checks depend on real navigation
completing in a real browser. As pytest tests sharing one browser across a
module they were NONDETERMINISTIC (the same file gave 1 failure twice then 9),
and a flaky safety test is worse than none — it teaches you to ignore red. So
the deterministic half lives in tests/test_extension_browser.py and this script
carries the live proof, the same split as harness_wake / harness_realbrowser_*.

    python tests/harness_extension_actions.py

Launches its own Chrome with the extension, serves local fixtures, and checks:
  click_navigates              — a link click actually moves the page
  js_jump_off_site_is_flagged  — a JS navigation with no href is reported after
  committal_click_confirms     — "Delete account" reaches CONFIRM, naming the site
  cross_host_gated_pre_click   — a cross-host anchor CONFIRMs with its destination
  enter_carries_the_payload    — Enter confirms showing what is submitted
  nameless_button_fails_closed — an unnamed actionable element CONFIRMs
  refused_without_allow_actions— the second opt-in is a real boundary
"""
from __future__ import annotations

import http.server
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import _harness_env  # noqa: E402,F401  (audit isolation: import BEFORE jarvis)
from jarvis import config                          # noqa: E402
from jarvis.core import extbridge                  # noqa: E402
from jarvis.core.settings_store import settings     # noqa: E402

EXT_DIR = ROOT / "extension"
FAILURES: list[str] = []

PAGES = {
    "/form": ("<title>Form</title><form>"
              "<label for='q'>Search</label>"
              "<input id='q' name='q' placeholder='search products'>"
              "<button type='submit'>Delete account</button></form>"
              "<a href='/other'>Read more</a>"),
    "/other": "<title>Other</title><p>arrived</p>",
    "/bare": "<title>Bare</title><form><button type='submit' id='b'></button></form>",
    "/linkto": "<title>Linkto</title><a href='http://localhost:9/elsewhere'>go elsewhere</a>",
    "/jsjump": ("<title>JsJump</title><button type='button' "
                "onclick=\"location.href='http://localhost:9/x'\">leave</button>"),
    "/search": "<title>Search</title><form><input name='q' placeholder='Search' autofocus></form>",
}


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


class _H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = PAGES.get(self.path.split("?")[0], "<title>Home</title><p>home</p>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())


def main() -> int:
    import hashlib
    path_for_id = str(EXT_DIR)
    path_for_id = path_for_id[0].upper() + path_for_id[1:]
    digest = hashlib.sha256(path_for_id.encode("utf-16-le")).hexdigest()[:32]
    ext_id = "".join(chr(ord("a") + int(c, 16)) for c in digest)
    settings.set("web.profile_mode", "extension", persist=False)
    settings.set("web.extension_id", ext_id, persist=False)
    settings.set("web.allow_actions", True, persist=False)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def serve():
        import uvicorn
        uvicorn.run("jarvis.server:app", host=config.SERVER_HOST,
                    port=config.SERVER_PORT, log_level="error")

    threading.Thread(target=serve, daemon=True).start()
    time.sleep(3)

    profile = Path(tempfile.mkdtemp(prefix="jarvis-actions-"))
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile), headless=False,
        args=[f"--disable-extensions-except={EXT_DIR}",
              f"--load-extension={EXT_DIR}",
              "--no-first-run", "--no-default-browser-check"])
    deadline = time.time() + 45
    while time.time() < deadline and not extbridge.bridge.connected():
        time.sleep(0.5)
    if not extbridge.bridge.connected():
        print("the extension never connected")
        ctx.close(); pw.stop(); shutil.rmtree(profile, ignore_errors=True)
        return 1

    from jarvis.primitives import web
    try:
        # 1. a link click actually moves the page
        web.navigate(base + "/form")
        time.sleep(0.5)
        r = web.click_element("Read more")
        check("click_navigates",
              bool(r.get("ok")) and "/other" in (web._active_session().current_url or ""),
              f"{r.get('message','')[:70]}")

        # 2. a JS jump off-site is FLAGGED (it cannot be pre-gated)
        web.navigate(base + "/jsjump")
        time.sleep(0.5)
        r = web.click_element("leave")
        msg = (r.get("message") or "").lower()
        check("js_jump_off_site_is_flagged",
              "localhost" in msg and "javascript" in msg, r.get("message", "")[:90])

        # 3. committal click CONFIRMS and names the site
        web.navigate(base + "/form")
        time.sleep(0.5)
        info = web.classify_web_click({"target": "Delete account"})
        check("committal_click_confirms",
              info["tier"] == "confirm" and "127.0.0.1" in info["description"],
              str(info)[:90])

        # 4. cross-host anchor gated BEFORE the click, carrying the destination
        web.navigate(base + "/linkto")
        time.sleep(0.5)
        info = web.classify_web_click({"target": "go elsewhere"})
        check("cross_host_gated_pre_click",
              info["tier"] == "confirm" and "localhost:9" in (info.get("command") or ""),
              str(info)[:90])

        # 5. Enter confirms carrying the field payload (slice 38)
        web.navigate(base + "/search")
        time.sleep(0.5)
        web.fill_field("Search", "transfer 5000")
        time.sleep(0.3)
        info = web.classify_web_key({"key": "enter"})
        check("enter_carries_the_payload",
              info["tier"] == "confirm" and "transfer 5000" in (info.get("command") or ""),
              str(info.get("command"))[:70])

        # 6. a nameless actionable element fails CLOSED
        web.navigate(base + "/bare")
        time.sleep(0.5)
        check("nameless_button_fails_closed",
              web.classify_web_click({"target": "submit"})["tier"] == "confirm")

        # 7. the second opt-in is a real boundary
        settings.set("web.allow_actions", False, persist=False)
        check("refused_without_allow_actions",
              web.classify_web_click({"target": "Delete account"})["tier"] == "blocked")
        settings.set("web.allow_actions", True, persist=False)
    finally:
        ctx.close()
        pw.stop()
        shutil.rmtree(profile, ignore_errors=True)
        srv.shutdown()

    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
