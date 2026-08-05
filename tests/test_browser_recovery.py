"""Slice 66 — the browser must survive its own window being closed.

The only LIVE browser bug in the owner's audit log (every other browser failure
predates 2026-07-19 and has not recurred):

    2026-08-04T17:41  browse_navigate {'url': 'https://www.youtube.com'}
      -> FAILED: Couldn't load ...: Page.goto: Target page, context or browser
         has been closed
    ... the identical error seven times, three of them inside one minute.

Cause: BrowserSession._ensure() checks whether the OWNER THREAD is alive, not
whether the browser that thread owns still is. Close the sandbox Chromium (or
let it crash) and the thread happily keeps serving a dead page forever, so every
later request fails identically until JARVIS is restarted.

These tests kill a real browser out from under a real session — no mocks for the
failure itself — using the same headless Chromium and loopback fixture server
the rest of test_web.py uses. No network, no live model.
"""
from __future__ import annotations

import http.server
import threading

import pytest

from jarvis.core.settings_store import settings
from jarvis.primitives import web


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><head><title>recovery fixture</title></head><body><h1>alive</h1></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s.server_address[1]
    s.shutdown()


@pytest.fixture(autouse=True, scope="module")
def _settings():
    settings.set("web.headless", True, persist=False)
    settings.set("web.timeout_s", 5, persist=False)
    settings.set("web.profile_mode", "isolated", persist=False)
    yield
    web.session.close()
    settings.set("web.headless", False, persist=False)
    settings.set("web.timeout_s", 15, persist=False)


def _kill_the_browser_underneath(sess) -> None:
    """Close the real Chromium while the owner thread keeps running — exactly
    the state the audit log recorded. The thread stays alive; its page dies."""
    def _boom(page):
        page.context.browser.close()
        return True
    sess._do(_boom)


def test_navigate_recovers_when_the_browser_was_closed(server):
    url = f"http://127.0.0.1:{server}/"
    first = web.session.navigate(url)
    assert first["url"].startswith("http://127.0.0.1"), first

    _kill_the_browser_underneath(web.session)

    second = web.session.navigate(url)
    assert second["url"].startswith("http://127.0.0.1"), second
    assert second.get("title") == "recovery fixture", second


def test_read_page_recovers_too(server):
    url = f"http://127.0.0.1:{server}/"
    web.session.navigate(url)
    _kill_the_browser_underneath(web.session)
    out = web.read_page()
    assert out["ok"] is True, out
    assert "alive" in out["text"], out["text"][:200]


def test_recovery_is_reported_not_silent(server):
    """A relaunch that hides itself would mask a genuinely broken browser. The
    caller-facing result has to admit the session was restarted."""
    url = f"http://127.0.0.1:{server}/"
    web.session.navigate(url)
    _kill_the_browser_underneath(web.session)
    out = web.navigate(url)
    assert out["ok"] is True, out
    assert "restart" in out["message"].lower() or "reopen" in out["message"].lower(), \
        out["message"]


def test_only_one_relaunch_is_attempted(monkeypatch, server):
    """A relaunch LOOP against a genuinely dead browser is worse than a clean
    failure: it would hang the chain instead of telling the user."""
    attempts: list[int] = []
    real_ensure = web.BrowserSession._ensure

    def counting_ensure(self):
        attempts.append(1)
        return real_ensure(self)

    monkeypatch.setattr(web.BrowserSession, "_ensure", counting_ensure)
    monkeypatch.setattr(
        web.BrowserSession, "_run_once",
        lambda self, fn, wait_s=None: (_ for _ in ()).throw(
            RuntimeError("Target page, context or browser has been closed")))

    with pytest.raises(Exception) as exc:
        web.session.navigate(f"http://127.0.0.1:{server}/")
    assert "closed" in str(exc.value).lower(), exc.value
    # one _ensure for the original attempt, one for the single relaunch
    assert len(attempts) <= 2, f"relaunched {len(attempts) - 1} times, expected 1"


def test_a_normal_error_is_not_treated_as_a_dead_browser(monkeypatch, server):
    """Only the closed-browser family triggers a relaunch. A 404, a timeout or a
    bad selector must surface as itself."""
    calls: list[int] = []

    def once(self, fn, wait_s=None):
        calls.append(1)
        raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")

    monkeypatch.setattr(web.BrowserSession, "_run_once", once)
    with pytest.raises(Exception) as exc:
        web.session.navigate("http://does-not-exist.invalid/")
    assert "ERR_NAME_NOT_RESOLVED" in str(exc.value), exc.value
    assert len(calls) == 1, f"retried a non-recoverable error {len(calls)} times"


def test_is_dead_browser_matches_the_real_error_strings():
    """The exact texts Playwright produced in the owner's log."""
    assert web._is_dead_browser(RuntimeError(
        "Page.goto: Target page, context or browser has been closed"))
    assert web._is_dead_browser(RuntimeError("No current window"))
    assert not web._is_dead_browser(RuntimeError("net::ERR_CONNECTION_REFUSED"))
    assert not web._is_dead_browser(RuntimeError("Timeout 15000ms exceeded"))


def test_recovery_returns_to_the_page_it_was_on(server):
    """Caught while building: a rebuilt browser starts on about:blank, so the
    first version of this fix made read_page() succeed and return an EMPTY
    page — silently wrong, which is worse than the failure it replaced."""
    url = f"http://127.0.0.1:{server}/"
    web.session.navigate(url)
    _kill_the_browser_underneath(web.session)
    out = web.read_page()
    assert out["ok"] is True, out
    assert "about:blank" not in out["text"], out["text"][:160]
    assert str(server) in out["text"], out["text"][:160]


# ------------------------------------------- slice 69b: the gate must fail CLOSED

def test_cross_host_is_assumed_when_the_current_page_is_unknown(monkeypatch):
    """Found via a flaky extension test, and it is a real hole, not a flake.

        AssertionError: {'tier': 'auto', ...}  assert 'auto' == 'confirm'

    _cross_host() read the session's current_url to decide whether a click
    leaves the site. When that is empty — a fresh session, a just-recovered one,
    a page whose URL has not committed — cur_host was None and the function
    returned None, which the callers read as "same host, no confirmation
    needed". So NOT KNOWING where we are made the gate fall OPEN.

    Slice 27 built this gate precisely so a click to another site is confirmed;
    slice 35 already established the doctrine for the tier system (unknown fails
    closed). This applies it here.
    """
    class _Blind:
        current_url = None

    monkeypatch.setattr(web, "_active_session", lambda: _Blind())
    assert web._cross_host("https://elsewhere.example/x") == "elsewhere.example"


def test_cross_host_is_still_none_for_the_same_host(monkeypatch):
    """The fix must not confirm every click on the page you are already on."""
    class _On:
        current_url = "https://site.example/a"

    monkeypatch.setattr(web, "_active_session", lambda: _On())
    assert web._cross_host("https://site.example/b") is None
    assert web._cross_host("https://other.example/b") == "other.example"


def test_a_non_web_target_is_still_not_cross_host(monkeypatch):
    """Only http(s) destinations are gated here; a mailto: or a relative jump
    is somebody else's problem and must not become a spurious confirm."""
    class _Blind:
        current_url = None

    monkeypatch.setattr(web, "_active_session", lambda: _Blind())
    assert web._cross_host("mailto:someone@example.com") is None
    assert web._cross_host("") is None
    assert web._cross_host("javascript:void(0)") is None
