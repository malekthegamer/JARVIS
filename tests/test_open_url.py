"""Slice 60 — `open_url`: hand a website to Windows instead of automating a browser.

MEASURED PROBLEM. The audit log holds 313 real actions and 51 failures (16%).
`browse_navigate` alone failed 10/42 (24%), with verbatim reasons like
"Your browser isn't connected to JARVIS" — because the ONLY signposted route for
"open YouTube" was the full browser-automation stack (isolated Playwright / CDP /
extension bridge), any of which can be misconfigured.

The owner's previous JARVIS never failed at this: it emitted
`Start-Process "https://youtube.com"` and Windows opened the real default
browser. The same mechanism already exists here (`apps.py` ends at
`os.startfile` for URIs) but was unreachable for a plain website.

SAFETY, and the reason this file is careful: `os.startfile` runs whatever it is
given. Handed "C:\\evil.exe" it EXECUTES it. So open_url is only allowed to pass
through http/https — everything else is refused outright, and that is the most
important property tested here.
"""
from __future__ import annotations

import pytest

from jarvis.primitives import apps


# ---------------- the happy path: it reaches the OS ----------------

def test_open_url_hands_the_url_to_the_operating_system(monkeypatch):
    """No Playwright, no CDP, no extension — just the OS default handler, which
    is the user's real browser with their real profile."""
    opened = []
    monkeypatch.setattr(apps.os, "startfile", opened.append, raising=False)

    r = apps.open_url("https://youtube.com")

    assert r["ok"], r
    assert opened == ["https://youtube.com"]


def test_a_bare_domain_gets_a_scheme(monkeypatch):
    """MEASURED FAILING TODAY: launch_app('youtube.com') missed because it has
    no scheme, so _is_uri() was False and it fell through to app resolution."""
    opened = []
    monkeypatch.setattr(apps.os, "startfile", opened.append, raising=False)

    assert apps.open_url("youtube.com")["ok"]
    assert apps.open_url("www.github.com")["ok"]
    assert opened == ["https://youtube.com", "https://www.github.com"]


def test_a_known_site_name_resolves(monkeypatch):
    """'open youtube' is what the user actually says. A tiny alias map beats
    dead-ending on the most common phrasing there is."""
    opened = []
    monkeypatch.setattr(apps.os, "startfile", opened.append, raising=False)

    assert apps.open_url("youtube")["ok"]
    assert opened == ["https://www.youtube.com"]


# ---------------- SAFETY: os.startfile executes what it is given -------------

@pytest.mark.parametrize("hostile", [
    r"C:\Windows\System32\calc.exe",
    r"C:\Users\someone\payload.bat",
    "file:///C:/Windows/System32/calc.exe",
    "javascript:alert(1)",
    r"\\attacker\share\evil.exe",
    "ms-settings:",
    "cmd.exe",
])
def test_anything_that_is_not_http_is_REFUSED(monkeypatch, hostile):
    """THE critical property. os.startfile RUNS a path — an unguarded open_url
    would be an arbitrary-execution primitive dressed as a browser helper. Only
    http/https may ever reach the OS."""
    opened = []
    monkeypatch.setattr(apps.os, "startfile", opened.append, raising=False)

    r = apps.open_url(hostile)

    assert r["ok"] is False, f"{hostile!r} must be refused: {r}"
    assert opened == [], f"{hostile!r} REACHED os.startfile — arbitrary execution"


def test_open_url_never_raises(monkeypatch):
    """Never-raise contract, same as every other primitive."""
    monkeypatch.setattr(apps.os, "startfile", lambda t: None, raising=False)
    for bad in (None, "", "   ", 123, "://", "https://"):
        out = apps.open_url(bad)
        assert isinstance(out, dict) and "ok" in out


def test_an_os_failure_is_reported_not_swallowed(monkeypatch):
    def boom(_t):
        raise OSError("no handler registered")
    monkeypatch.setattr(apps.os, "startfile", boom, raising=False)

    r = apps.open_url("https://example.com")
    assert r["ok"] is False and "example.com" in r["message"]


# ---------------- the gate: reuse slice 59's verified logic ----------------

def test_a_site_the_user_named_opens_without_a_prompt(monkeypatch):
    """The whole point: "open YouTube" should just happen."""
    from jarvis.core import chain
    from jarvis import primitives

    t = chain.start()
    t.user_message = "open youtube please"
    info = primitives.PRIMITIVES["open_url"]["classify"]({"url": "https://youtube.com"})
    assert info["tier"] == "auto", info


def test_a_host_the_MODEL_discovered_still_confirms(monkeypatch):
    """SLICE 59'S GUARANTEE MUST NOT REGRESS THROUGH A NEW DOOR. A page that
    says "now visit evil.example" produces a URL the user never named, and this
    verb must gate it exactly like browse_navigate does."""
    from jarvis.core import chain
    from jarvis import primitives

    t = chain.start()
    t.user_message = "read this page and summarise it"
    info = primitives.PRIMITIVES["open_url"]["classify"]({"url": "https://evil.example/x"})
    assert info["tier"] == "confirm", info
    assert "evil.example" in (info.get("command", "") + info["description"])


def test_a_non_http_url_is_BLOCKED_at_the_gate_too():
    """Defence in depth: refused by the classifier AND by open_url itself."""
    from jarvis import primitives

    info = primitives.PRIMITIVES["open_url"]["classify"](
        {"url": r"C:\Windows\System32\calc.exe"})
    assert info["tier"] == "blocked", info


# ---------------- wiring ----------------

def test_open_url_is_registered_and_rides_no_kill_switch():
    """Opening a page in your own browser is not browser AUTOMATION, so it must
    not be withheld by web.enabled — that switch exists to stop JARVIS driving
    a browser, not to stop it opening a link."""
    from jarvis import primitives

    assert "open_url" in primitives.PRIMITIVES
    for key, verbs in primitives._KILL_SWITCHES.items():
        assert "open_url" not in verbs or key == "web.enabled", key
