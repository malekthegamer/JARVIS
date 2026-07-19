"""Slice 28 — the read-only audit-viewer HTTP API.

Envelope-first / reveal-on-demand: GET /api/audit returns only the plaintext
envelope (never decrypted args/result); GET /api/audit/{index}/payload is the
sole path that decrypts one record, and only when explicitly asked. All tests
run through TestClient against the process-wide audit_log singleton, which the
autouse conftest fixture (_isolated_audit_log) has already swapped to a
per-test temp file — so seeding via audit.audit_log.record() is isolated.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from jarvis import server
from jarvis.core import audit, dpapi

MARKER = "SECRET_MARKER_xyz"


@pytest.fixture()
def client():
    return TestClient(server.app)


def _seed(**kw):
    """Write one record to the isolated singleton; defaults are innocuous."""
    kw.setdefault("tool", "launch_app")
    kw.setdefault("tier", "auto")
    kw.setdefault("status", "ok")
    kw.setdefault("args", {"name": "notepad"})
    kw.setdefault("result", "OK")
    assert audit.audit_log.record(**kw)


# ------------------------------------------------------------------- list

def test_list_returns_envelopes_newest_first(client):
    _seed(tool="launch_app")
    _seed(tool="set_volume")
    r = client.get("/api/audit")
    assert r.status_code == 200
    rows = r.json()["records"]
    assert [x["tool"] for x in rows] == ["set_volume", "launch_app"]  # newest first
    assert [x["index"] for x in rows] == [1, 0]                       # file index kept
    for x in rows:
        for k in ("ts", "tool", "tier", "gate", "status", "dry_run", "has_payload"):
            assert k in x
        assert "args" not in x and "payload" not in x                # envelope only


def test_list_respects_tail_limit(client):
    for i in range(5):
        _seed(tool=f"tool{i}")
    rows = client.get("/api/audit?tail=2").json()["records"]
    assert [x["tool"] for x in rows] == ["tool4", "tool3"]           # last 2, newest first


def test_list_omits_decrypted_payload(client):
    """THE privacy contract: the timeline view must never carry decrypted
    args/result — the marker lives only behind an explicit reveal."""
    if not dpapi.available():
        pytest.fail("DPAPI must be available on this machine (pywin32)")
    _seed(tool="run_shell", tier="confirm", gate="approved",
          args={"command": MARKER}, result=f"ran {MARKER}")
    body = client.get("/api/audit").text
    assert MARKER not in body, "decrypted arg leaked into the list response"


def test_list_empty_log_is_honest_empty(client):
    r = client.get("/api/audit")
    assert r.status_code == 200 and r.json()["records"] == []


# ------------------------------------------------------------------- reveal

def test_reveal_returns_decrypted_payload_for_index(client):
    if not dpapi.available():
        pytest.fail("DPAPI must be available on this machine (pywin32)")
    _seed(tool="run_shell", tier="confirm", gate="approved",
          args={"command": MARKER}, result=f"ran {MARKER}")
    r = client.get("/api/audit/0/payload")
    assert r.status_code == 200
    payload = r.json()["payload"]
    assert payload["args"] == {"command": MARKER}
    assert MARKER in json.dumps(payload)


def test_reveal_out_of_range_index_is_honest_not_crash(client):
    _seed()
    r = client.get("/api/audit/99/payload")
    assert r.status_code == 200
    body = r.json()
    assert body["payload"] is None and body.get("payload_error")


def test_reveal_surfaces_payload_error_when_undecryptable(client, monkeypatch):
    def boom(_data):
        raise RuntimeError("DPAPI down")
    monkeypatch.setattr(dpapi, "protect", boom)
    monkeypatch.setattr(dpapi, "available", lambda: False)
    _seed(tool="send_email", tier="confirm", args={"body": MARKER}, result=MARKER)
    r = client.get("/api/audit/0/payload")
    assert r.status_code == 200
    body = r.json()
    assert body["payload"] is None and "unavailable" in body["payload_error"].lower()


# ------------------------------------------------------------------- page + wiring

def test_audit_page_served(client):
    r = client.get("/audit")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_endpoints_read_the_singleton(client):
    """Proof the endpoint reads audit.audit_log (the swapped temp singleton),
    not the real data/audit log: a freshly seeded tool shows up."""
    _seed(tool="uniquetool_zzz")
    rows = client.get("/api/audit").json()["records"]
    assert any(x["tool"] == "uniquetool_zzz" for x in rows)
