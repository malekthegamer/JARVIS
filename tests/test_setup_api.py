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


def test_setup_state_respects_origin_guard(client):
    """Slice 36's guard must cover every new endpoint automatically."""
    r = client.get("/api/setup_state",
                   headers={"origin": "https://evil.example.com"})
    assert r.status_code == 403
