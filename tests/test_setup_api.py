"""Slice 37: /api/setup_state — the first-run wizard's detection endpoint.

The HUD needs to know whether a brain key is configured so it can show the
setup panel instead of a dead assistant. The endpoint returns BOOLEANS ONLY:
the key itself must never cross the wire, even to localhost (same posture as
the slice-28 audit viewer, which never decrypts a payload for a browse).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

MARKER = "AIzaTESTKEYMARKER1234567890abcdef"


@pytest.fixture()
def client():
    from jarvis import server
    with TestClient(server.app) as c:
        yield c


def test_setup_state_reports_missing_key(client, monkeypatch):
    from jarvis import config
    monkeypatch.setattr(config, "get_api_key", lambda p: None)
    body = client.get("/api/setup_state").json()
    assert body["brain_key"] is False


def test_setup_state_reports_configured_key(client, monkeypatch):
    from jarvis import config
    monkeypatch.setattr(config, "get_api_key", lambda p: MARKER)
    body = client.get("/api/setup_state").json()
    assert body["brain_key"] is True


def test_setup_state_never_leaks_the_key(client, monkeypatch):
    """Privacy pin: a seeded key marker must be absent from the raw response."""
    from jarvis import config
    monkeypatch.setattr(config, "get_api_key", lambda p: MARKER)
    raw = client.get("/api/setup_state").text
    assert MARKER not in raw, "the API key leaked into /api/setup_state"
    assert MARKER[:12] not in raw


def test_hud_settings_audit_links_open_in_a_separate_tab():
    """v1.0.2: the ⚙/🗎 links were plain same-tab navigations, so opening
    Settings or Audit navigated AWAY from the HUD and wiped its in-page
    conversation transcript on return. They must carry a target so the HUD tab
    is never left."""
    from jarvis import config
    html = (config.BASE_DIR / "jarvis" / "static" / "index.html").read_text(encoding="utf-8")
    import re
    for href in ("/settings", "/audit"):
        m = re.search(r'<a\b[^>]*href="%s"[^>]*>' % re.escape(href), html)
        assert m, f"no <a href={href}> in the HUD header"
        assert "target=" in m.group(0), \
            f"{href} link must open in a separate tab (target=), not navigate the HUD away"


def test_setup_state_get_allowed_but_not_cors_readable(client):
    """v1.0.0 hotfix: this is a safe GET, so the server allows it (the guard is
    for the WebSocket + mutating methods). The cross-origin READ is blocked by
    the browser instead — the response carries no Access-Control-Allow-Origin,
    so a foreign page's fetch() can't see the body. Booleans only regardless."""
    r = client.get("/api/setup_state",
                   headers={"origin": "https://evil.example.com"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
