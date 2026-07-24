"""Slice 36: the Origin guard — the HUD transport is localhost-bound but was
UNAUTHENTICATED, and WebSockets are exempt from the same-origin policy.

The hole (verified live against a running server before the fix): a handshake
carrying `Origin: https://evil.example.com` was ACCEPTED and immediately
received state. Since server._clients is broadcast to indiscriminately, a
malicious page also received every `confirm_request` — INCLUDING its id — and
could reply `{"type":"confirm_response","approved":true}`, approving its own
prompt. That defeats the CONFIRM gate, the single load-bearing control in the
whole safety model: shell execution, delete-anywhere and email all sit behind
it. Any website the user visited while JARVIS ran could drive the agent.

`test_foreign_origin_cannot_resolve_a_confirmation` is the exploit itself,
kept as a permanent regression pin and red-checked against the unfixed server.
"""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

EVIL = "https://evil.example.com"


@pytest.fixture()
def client():
    from jarvis import server
    with TestClient(server.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _broadcaster_back_to_idle():
    """Leak guard (slice 18 pattern): this file drives real gate machinery."""
    yield
    from jarvis.state import AgentState, broadcaster
    broadcaster.set(AgentState.IDLE)


def _hud_origin() -> str:
    from jarvis import config
    return f"http://{config.SERVER_HOST}:{config.SERVER_PORT}"


# ---------- WebSocket ----------

def test_ws_rejects_foreign_origin(client):
    """The core fix: a browser tab on any other site cannot open the socket."""
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": EVIL}) as ws:
            ws.receive_json()      # must never deliver a frame


def test_ws_accepts_hud_origin(client):
    """The legitimate HUD must keep working — a guard that breaks the real
    client is not a fix."""
    with client.websocket_connect("/ws", headers={"origin": _hud_origin()}) as ws:
        first = ws.receive_json()
    assert first["type"] == "state"


def test_ws_allows_missing_origin_for_nonbrowser_client(client):
    """Deliberate, documented design: browsers ALWAYS send Origin on a WS
    handshake, so rejecting only a present-and-foreign Origin closes the whole
    browser attack surface. A local non-browser client (pytest, a harness,
    curl) is already arbitrary local code execution — gating it buys nothing.
    Pinned so the intent can't be 'tightened' away without a decision."""
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "state"


def test_foreign_origin_cannot_resolve_a_confirmation(client):
    """THE EXPLOIT, as a permanent pin.

    A real CONFIRM is raised on a worker thread; a foreign-origin socket then
    tries to approve it. Pre-fix, the attacker page received the confirm_request
    (id included) and its approval was honoured. Post-fix it cannot even
    connect, so the request must resolve DENIED by timeout.
    """
    from starlette.websockets import WebSocketDisconnect

    from jarvis.core.confirmations import confirmations

    seen: dict = {}

    def _capture(event):
        if event.get("type") == "confirm_request":
            seen["id"] = event.get("id")

    unsub = confirmations.subscribe(_capture)
    result: dict = {}

    def _ask():
        decision = confirmations.request("delete every file", timeout_s=3.0)
        result["approved"] = decision.approved

    worker = threading.Thread(target=_ask)
    worker.start()
    try:
        for _ in range(50):            # wait for the prompt to be pending
            if seen.get("id"):
                break
            threading.Event().wait(0.05)
        assert seen.get("id"), "fixture failure: no confirm_request was raised"

        # The attack: connect from a foreign origin and approve the prompt.
        try:
            with client.websocket_connect("/ws", headers={"origin": EVIL}) as ws:
                ws.send_json({"type": "confirm_response",
                              "id": seen["id"], "approved": True})
        except WebSocketDisconnect:
            pass                       # expected once the guard is in place
    finally:
        worker.join(timeout=10)
        unsub()

    assert result.get("approved") is False, \
        "a foreign-origin page APPROVED a CONFIRM — the gate is bypassed"


# ---------- HTTP ----------

def test_http_post_rejects_foreign_origin(client):
    """CSRF: absent CORS headers a browser can't READ the reply, but the POST
    is still SENT and acted on. /api/settings mutates capability switches."""
    r = client.post("/api/settings", json={"settings": {}},
                    headers={"origin": EVIL})
    assert r.status_code == 403, r.status_code


def test_http_post_accepts_hud_origin(client):
    r = client.post("/api/settings", json={"settings": {}},
                    headers={"origin": _hud_origin()})
    assert r.status_code == 200, r.status_code


def test_sec_fetch_site_cross_site_rejected(client):
    """Defence in depth: browsers always set Sec-Fetch-Site, so this catches a
    cross-site request even if Origin were somehow absent."""
    r = client.post("/api/settings", json={"settings": {}},
                    headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403, r.status_code
