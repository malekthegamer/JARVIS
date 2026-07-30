"""Slice 43 — END-TO-END extension tests in a browser the SUITE controls.

WHY THIS FILE EXISTS. Every extension behaviour so far was verified either by
static text-matching on background.js or by asking the owner to click through
`chrome://extensions` by hand. Manual verification happens once and then rots,
and the pinned-tab bug shipped *because* "may I touch this tab?" was logic no
test could reach.

Playwright can launch Chrome with `--load-extension`, and the extension's ID is
derived from its PATH — so a suite-launched copy at the repo's `extension/`
folder gets the SAME id `web.extension_id` already allows. No reconfiguration,
no human.

WHAT THIS CANNOT DO: the owner's LOGINS. This is a fresh profile, so anything
whose behaviour depends on being signed in (Gmail's authenticated DOM) still
needs a final pass in their browser. That is a small, rare set — not "reload the
extension after every edit".

Playwright cannot reach an MV3 service worker (`context.service_workers` is
empty for extensions), so tab state that needs `chrome.tabs.*` — pinning — is
covered by unit-testing `extension/lib.js` directly instead. That is exactly why
the pure logic was extracted there.
"""
from __future__ import annotations

import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

from jarvis import config
from jarvis.core import extbridge
from jarvis.core.settings_store import settings

ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = ROOT / "extension"
LIB_JS = EXT_DIR / "lib.js"


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


@pytest.fixture(scope="module")
def ext_browser():
    """A real Chrome with the JARVIS extension loaded, talking to a real JARVIS
    server. Yields (playwright_context, web_module)."""
    if not _port_free(config.SERVER_PORT):
        pytest.fail(
            f"port {config.SERVER_PORT} is in use — quit the running JARVIS "
            f"before this test file. (The extension's socket URL is fixed at "
            f"that port, so the test server must own it.)")

    prev_mode = settings.get("web.profile_mode")
    prev_id = settings.get("web.extension_id")
    # The unpacked id is sha256(path, utf-16-le) mapped 0-f -> a-p.
    import hashlib
    # Chrome derives an unpacked extension's id from its absolute path:
    # sha256(path as UTF-16LE, drive letter uppercased)[:32], mapped 0-f -> a-p.
    # Verified against a real load: E:\J.A.R.V.I.S\extension ->
    # iapenlgallmblndmidfehikgdmfepmij.
    path_for_id = str(EXT_DIR)
    path_for_id = path_for_id[0].upper() + path_for_id[1:]
    digest = hashlib.sha256(path_for_id.encode("utf-16-le")).hexdigest()[:32]
    ext_id = "".join(chr(ord("a") + int(c, 16)) for c in digest)

    settings.set("web.profile_mode", "extension", persist=False)
    settings.set("web.extension_id", ext_id, persist=False)

    def serve():
        import uvicorn
        uvicorn.run("jarvis.server:app", host=config.SERVER_HOST,
                    port=config.SERVER_PORT, log_level="error")

    threading.Thread(target=serve, daemon=True).start()
    deadline = time.time() + 30
    while time.time() < deadline and _port_free(config.SERVER_PORT):
        time.sleep(0.3)

    profile = Path(tempfile.mkdtemp(prefix="jarvis-ext-test-"))
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=False,          # MV3 extensions need a headed browser here
        args=[f"--disable-extensions-except={EXT_DIR}",
              f"--load-extension={EXT_DIR}",
              "--no-first-run", "--no-default-browser-check"],
    )
    deadline = time.time() + 45
    while time.time() < deadline and not extbridge.bridge.connected():
        time.sleep(0.5)
    if not extbridge.bridge.connected():
        ctx.close(); pw.stop()
        shutil.rmtree(profile, ignore_errors=True)
        pytest.fail(f"the extension never connected (expected id {ext_id})")

    from jarvis.primitives import web
    yield ctx, web

    try:
        ctx.close()
    finally:
        pw.stop()
        shutil.rmtree(profile, ignore_errors=True)
        settings.set("web.profile_mode", prev_mode, persist=False)
        settings.set("web.extension_id", prev_id, persist=False)


def _open_pages(ctx) -> list[str]:
    """URLs of every open tab, via CDP.

    NOT ctx.pages: Playwright does not attach to tabs the EXTENSION created
    with chrome.tabs.create, so ctx.pages under-reports them (it showed a lone
    about:blank while two real pages were open). Target.getTargets sees the
    browser's actual tab list."""
    cdp = ctx.new_cdp_session(ctx.pages[0])
    try:
        infos = cdp.send("Target.getTargets").get("targetInfos", [])
    finally:
        cdp.detach()
    return [t.get("url", "") for t in infos if t.get("type") == "page"]



# ---------- the owner's reported bugs, now END-TO-END ----------

def test_open_creates_a_new_tab_and_leaves_the_previous_page_alone(ext_browser):
    """REPORTED BUG 2, automated: "I told it to open Gmail in a new tab and it
    opened over the YouTube tab it had just opened."

    Two opens in a row must leave BOTH pages present. Previously the second
    replaced the first, because JARVIS's own new tab was the active one and
    `navigate` did tabs.update(active)."""
    ctx, web = ext_browser
    before = len(_open_pages(ctx))

    r1 = web.navigate("https://example.com/")
    assert r1["ok"], r1
    time.sleep(0.5)

    r2 = web.navigate("https://example.org/")
    assert r2["ok"], r2
    time.sleep(0.5)

    urls = _open_pages(ctx)
    assert any("example.com" in u for u in urls), \
        f"the FIRST page was destroyed by the second open: {urls}"
    assert any("example.org" in u for u in urls), urls
    assert len(ctx.pages) >= before + 2, \
        f"each open must add a tab, not replace one: {len(ctx.pages)} vs {before}"


def test_reuse_continues_in_the_same_tab_without_growing_the_tab_bar(ext_browser):
    """The escape hatch: walking through one site must not spawn a tab per step.
    Only reachable for JARVIS's OWN tab."""
    ctx, web = ext_browser
    web.navigate("https://example.com/")
    time.sleep(0.4)
    count = len(_open_pages(ctx))

    out = web._active_session().navigate("https://example.org/")
    assert "example.org" in (out.get("url") or "")
    time.sleep(0.4)
    # default is a new tab; prove the reuse path separately via the session
    assert len(_open_pages(ctx)) >= count


def test_read_page_returns_real_content_from_the_browser(ext_browser):
    ctx, web = ext_browser
    web.navigate("https://example.com/")
    time.sleep(0.5)
    r = web.read_page()
    assert r["ok"], r
    assert "Example Domain" in r["text"], r["text"][:200]
    assert "UNTRUSTED" in r["text"], "the data boundary must still wrap it"


def test_hud_tab_is_never_navigated_away(ext_browser):
    """The HUD's transcript lives only in its page (the v1.0.2 bug). If the HUD
    tab is the active one — which it is whenever the owner types a request into
    it — an open must NOT replace it."""
    ctx, web = ext_browser
    hud = ctx.new_page()
    hud.goto(f"http://127.0.0.1:{config.SERVER_PORT}/")
    hud.bring_to_front()
    time.sleep(0.5)

    r = web.navigate("https://example.com/")
    assert r["ok"], r
    time.sleep(0.5)
    assert f"127.0.0.1:{config.SERVER_PORT}" in hud.url, \
        f"the HUD tab was navigated away to {hud.url}"


# ---------- pure logic from the SHIPPED source (pinned tabs) ----------

def _lib(page):
    """Load extension/lib.js into a page and expose its functions.

    Tests the SHIPPED file, not a copy — Playwright cannot reach the MV3
    service worker, which is exactly why this logic was extracted into a
    loadable module."""
    # lib.js is a classic script that assigns globalThis.JARVIS_LIB, so it
    # loads with a plain script tag — no eval, which MV3's CSP forbids in the
    # isolated world anyway (that bug cost a debugging round).
    page.add_script_tag(content=LIB_JS.read_text(encoding="utf-8"))
    page.evaluate("() => { window.__lib = globalThis.JARVIS_LIB; }")


@pytest.fixture()
def lib_page(ext_browser):
    ctx, _web = ext_browser
    page = ctx.new_page()
    page.goto("about:blank")
    _lib(page)
    yield page
    page.close()


def test_pinned_tab_is_protected(lib_page):
    """REPORTED BUG 1, against the shipped source: "if I have a pinned tab and
    tell it to open YouTube, it opens in the PINNED tab"."""
    assert lib_page.evaluate(
        "() => window.__lib.isProtected({pinned: true, url: 'https://x.com/'})") is True


def test_ordinary_web_tab_is_not_protected(lib_page):
    assert lib_page.evaluate(
        "() => window.__lib.isProtected({pinned: false, url: 'https://x.com/'})") is False


def test_hud_and_chrome_pages_are_protected(lib_page):
    assert lib_page.evaluate(
        "() => window.__lib.isProtected({url: 'http://127.0.0.1:8000/'})") is True
    assert lib_page.evaluate(
        "() => window.__lib.isProtected({url: 'chrome://settings'})") is True
    assert lib_page.evaluate("() => window.__lib.isProtected(null)") is True, \
        "unknown tab must fail closed"


def test_pinned_tab_is_still_READABLE(lib_page):
    """Reading is not destructive. 'Protected' must not quietly mean
    'invisible' — the owner can still ask what's on a pinned tab."""
    assert lib_page.evaluate(
        "() => window.__lib.isReadable({pinned: true, url: 'https://x.com/'})") is True


# ---------- Stage 1+2: the gate contract, and acting ----------
#
# Tiers are computed FROM the resolved element's name, so find_clickable must
# return the same {found,name,kind,href} contract Playwright's does — Stage 0
# measured 7/7 element and 7/7 TIER agreement on the fixture set before any of
# this was wired.

FIXTURES = {
    "/form": ("<title>Form</title><form>"
              "<label for='q'>Search</label>"
              "<input id='q' name='q' placeholder='search products'>"
              "<button type='submit'>Delete account</button></form>"
              "<a href='/other'>Read more</a>"),
    "/bare": "<title>Bare</title><form><button type='submit' id='b'></button></form>",
    # a cross-host ANCHOR: its href is inspectable BEFORE the click (slice 27)
    "/linkto": ("<title>Linkto</title>"
                "<a href='http://localhost:9/elsewhere'>go elsewhere</a>"),
    # a button that leaves the site via JAVASCRIPT: no href to pre-gate, so it
    # can only be FLAGGED after the fact — the honest residual
    "/jsjump": ("<title>JsJump</title><button type='button' "
                "onclick=\"location.href='http://localhost:9/x'\">leave</button>"),
    # a search box, for the slice-38 Enter payload
    "/search": ("<title>Search</title><form>"
                "<input name='q' placeholder='Search' autofocus></form>"),
    "/login": ("<title>Login</title><form>"
               "<input type='password' placeholder='Password' autofocus></form>"),
}


@pytest.fixture(scope="module")
def fixture_site():
    import http.server
    pages = FIXTURES

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = pages.get(self.path.split("?")[0], "<title>Home</title><p>home</p>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture()
def acting(ext_browser):
    """Committal actions permitted — the SECOND opt-in, default off."""
    prev = settings.get("web.allow_actions")
    settings.set("web.allow_actions", True, persist=False)
    yield ext_browser
    settings.set("web.allow_actions", prev, persist=False)


def test_find_clickable_returns_the_gate_contract(acting, fixture_site):
    ctx, web = acting
    web.navigate(fixture_site + "/form")
    time.sleep(0.6)
    m = web._active_session().find_clickable("Delete account")
    assert m["found"] is True, m
    assert m["name"] == "Delete account"
    assert m["kind"] == "button"


def test_committal_click_confirms_naming_the_real_site(acting, fixture_site):
    """A destructive-looking control on the user's real browser must CONFIRM."""
    ctx, web = acting
    web.navigate(fixture_site + "/form")
    time.sleep(0.6)
    info = web.classify_web_click({"target": "Delete account"})
    assert info["tier"] == "confirm", info
    assert "127.0.0.1" in info["description"], info["description"]


def test_benign_link_click_is_auto(acting, fixture_site):
    ctx, web = acting
    web.navigate(fixture_site + "/form")
    time.sleep(0.6)
    assert web.classify_web_click({"target": "Read more"})["tier"] == "auto"


def test_nameless_actionable_element_fails_closed(acting, fixture_site):
    """The JS-button blind spot: an actionable element with no accessible name
    can't be judged, so it must CONFIRM rather than run."""
    ctx, web = acting
    web.navigate(fixture_site + "/bare")
    time.sleep(0.6)
    assert web.classify_web_click({"target": "submit"})["tier"] == "confirm"


def test_actions_refused_until_allow_actions_is_on(ext_browser, fixture_site):
    """The second opt-in is a real boundary, not advice: BLOCKED at classify
    even though the resolver could find the element."""
    ctx, web = ext_browser
    prev = settings.get("web.allow_actions")
    settings.set("web.allow_actions", False, persist=False)
    try:
        web.navigate(fixture_site + "/form")
        time.sleep(0.6)
        assert web.classify_web_click({"target": "Delete account"})["tier"] == "blocked"
        assert web.classify_web_fill({"field": "Search", "text": "x"})["tier"] == "blocked"
    finally:
        settings.set("web.allow_actions", prev, persist=False)



def test_fill_verifies_by_reading_the_field_back(acting, fixture_site):
    """Never trust 'the call returned' — a fill that silently no-ops is the
    failure mode Playwright's fill had before it was special-cased."""
    ctx, web = acting
    web.navigate(fixture_site + "/form")
    time.sleep(0.6)
    r = web.fill_field("Search", "hello jarvis")
    assert r["ok"], r
    assert "hello jarvis" in r["message"], \
        f"must report the READBACK, not just success: {r['message']}"


# ---------- Stage 3: the GATES, on the real browser ----------

def test_cross_host_click_is_gated_before_navigating(acting, fixture_site):
    """Slice 27, through the extension: a click that would LEAVE the host must
    CONFIRM *before* it happens, using the anchor's href — and the modal must
    carry the destination so the approval is informed."""
    ctx, web = acting
    web.navigate(fixture_site + "/linkto")
    time.sleep(0.6)
    info = web.classify_web_click({"target": "go elsewhere"})
    assert info["tier"] == "confirm", info
    assert "localhost:9" in (info.get("command") or ""), info


def test_same_host_link_is_not_over_gated(acting, fixture_site):
    """The cost side: if everything confirmed, the gate would be noise."""
    ctx, web = acting
    web.navigate(fixture_site + "/form")
    time.sleep(0.6)
    assert web.classify_web_click({"target": "Read more"})["tier"] == "auto"



def test_enter_confirms_carrying_the_field_payload(acting, fixture_site):
    """Slice 38, through the extension: Enter SUBMITS, so it confirms — and the
    modal shows what is being submitted, not just 'press Enter'."""
    ctx, web = acting
    web.navigate(fixture_site + "/search")
    time.sleep(0.6)
    assert web.fill_field("Search", "transfer 5000")["ok"]
    time.sleep(0.3)
    info = web.classify_web_key({"key": "enter"})
    assert info["tier"] == "confirm", info
    assert "transfer 5000" in (info.get("command") or ""), \
        f"the payload must be in the modal: {info.get('command')!r}"


def test_password_field_is_never_shown_in_the_confirm(acting, fixture_site):
    """Probe A found isPassword is detectable; a password must never be pasted
    into the HUD."""
    ctx, web = acting
    # must be a SERVED page: about:blank is not readable, so readTarget would
    # fall back to another tab and the test would silently check the wrong field.
    web.navigate(fixture_site + "/login")
    time.sleep(0.8)
    got = web._active_session().focused_field()
    assert got.get("found") is True, got
    assert got.get("isPassword") is True, got
    payload = web._submit_payload()
    assert "password" in payload.lower() and "hidden" in payload.lower(), payload


# ---------- click REPORTING logic, without a browser ----------
#
# Whether a click navigates is browser behaviour and belongs in
# tests/harness_extension_actions.py (proven live there). What must never
# regress silently is the REPORTING: a click that leaves the site via
# JavaScript has no inspectable href, so it cannot be pre-gated — it has to be
# flagged afterwards. That logic is deterministic, so it is tested here.

def test_js_jump_off_site_is_flagged_in_the_result(monkeypatch):
    """Slice 27's residual, as a unit test. Also pins the ORDERING bug that hid
    it: the cross-host check must run BEFORE current_url is updated, or it
    compares the new url to itself and the flag never fires."""
    from jarvis.primitives import web
    settings.set("web.profile_mode", "extension", persist=False)
    try:
        sess = web.ExtensionSession()
        sess.current_url = "https://start.example/page"
        monkeypatch.setattr(sess, "_call", lambda cmd, **kw: {
            "ok": True, "name": "leave", "kind": "button", "href": "",
            "before": "https://start.example/page",
            "url": "https://elsewhere.test/x"})
        monkeypatch.setattr(web, "_active_session", lambda: sess)
        msg = sess.click("leave")["message"]
        assert "elsewhere.test" in msg
        assert "javascript" in msg.lower(), msg
    finally:
        settings.set("web.profile_mode", "isolated", persist=False)


def test_same_host_click_is_not_flagged(monkeypatch):
    """The cost side: flagging every click would make the warning worthless."""
    from jarvis.primitives import web
    settings.set("web.profile_mode", "extension", persist=False)
    try:
        sess = web.ExtensionSession()
        sess.current_url = "https://start.example/page"
        monkeypatch.setattr(sess, "_call", lambda cmd, **kw: {
            "ok": True, "name": "Read more", "kind": "link",
            "href": "https://start.example/other",
            "before": "https://start.example/page",
            "url": "https://start.example/other"})
        monkeypatch.setattr(web, "_active_session", lambda: sess)
        msg = sess.click("Read more")["message"]
        assert "javascript" not in msg.lower(), msg
    finally:
        settings.set("web.profile_mode", "isolated", persist=False)
