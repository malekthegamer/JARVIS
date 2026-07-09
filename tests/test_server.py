"""Stage-4 exit tests: the WS chat path walks the full state sequence in
order, transcripts render, the busy guard rejects re-entrancy, and events
arrive over one ordered pipe."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.providers.brain.base import BrainProvider, BrainResponse


class FakeProvider(BrainProvider):
    supports_tools = True

    def is_configured(self):
        return True

    def generate(self, messages, system_prompt, tools=None):
        return BrainResponse(text="Acknowledged, sir.")


@pytest.fixture()
def client(monkeypatch):
    from jarvis import server
    from jarvis.brain import jarvis_brain
    from jarvis.voice.voice_manager import voice_manager

    monkeypatch.setattr(jarvis_brain, "_provider_override", FakeProvider())
    # No real audio in tests — but keep the state contract speak() provides.
    from jarvis.state import AgentState, broadcaster

    def fake_speak(text):
        if not text:
            return
        broadcaster.set(AgentState.SPEAKING)
        broadcaster.set(AgentState.IDLE)

    monkeypatch.setattr(voice_manager, "speak", fake_speak)
    jarvis_brain.reset()
    with TestClient(server.app) as c:
        yield c


def _drain_until_idle_after_speaking(ws, limit=30):
    """Collect events until we see IDLE arrive after SPEAKING."""
    events = []
    seen_speaking = False
    for _ in range(limit):
        event = ws.receive_json()
        events.append(event)
        if event.get("type") == "state" and event.get("state") == "speaking":
            seen_speaking = True
        if seen_speaking and event.get("type") == "state" and event.get("state") == "idle":
            return events
    raise AssertionError(f"never reached speaking->idle; got: {events}")


def test_state_endpoint(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    assert r.json()["state"] == "idle"


def test_ws_chat_full_flow(client):
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()  # state sync on connect
        assert hello["type"] == "state"

        ws.send_json({"type": "chat", "text": "status report"})
        events = _drain_until_idle_after_speaking(ws)

        transcripts = [(e["who"], e["text"]) for e in events if e["type"] == "transcript"]
        assert ("user", "status report") in transcripts
        assert ("jarvis", "Acknowledged, sir.") in transcripts

        states = [e["state"] for e in events if e["type"] == "state"]
        # Ordered walk: thinking must precede speaking, which precedes final idle.
        assert states.index("thinking") < states.index("speaking")
        assert states[-1] == "idle"

        seqs = [e["seq"] for e in events if e["type"] == "state" and e.get("seq")]
        assert seqs == sorted(seqs), f"state events out of order: {seqs}"


def test_ws_empty_and_whitespace_chat_ignored(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # hello
        ws.send_json({"type": "chat", "text": "   "})
        ws.send_json({"type": "chat", "text": "real message"})
        events = _drain_until_idle_after_speaking(ws)
        user_lines = [e["text"] for e in events
                      if e["type"] == "transcript" and e["who"] == "user"]
        assert user_lines == ["real message"]


def test_busy_guard_rejects_reentrant_listen(client):
    from jarvis import server
    assert server._busy.acquire(blocking=False)
    try:
        r = client.post("/api/listen")
        assert r.status_code == 409
    finally:
        server._busy.release()


# ---------- slice 3: confirm flow over the WebSocket ----------

import threading
import time

from jarvis.core.confirmations import confirmations
from jarvis.primitives import files
from jarvis.providers.brain.base import ToolCall


class DeleteProposingProvider(BrainProvider):
    """Round 1: propose delete_file. Round 2: report the tool result."""
    supports_tools = True

    def is_configured(self):
        return True

    def __init__(self):
        self.rounds = 0

    def generate(self, messages, system_prompt, tools=None):
        self.rounds += 1
        if self.rounds == 1:
            return BrainResponse(tool_calls=[
                ToolCall(id="t1", name="delete_file", args={"name": "ws-test.txt"})])
        result = next(m["content"] for m in reversed(messages) if m["role"] == "tool")
        return BrainResponse(text=f"Result: {result}")


@pytest.fixture()
def confirm_client(monkeypatch):
    from jarvis import server
    from jarvis.brain import jarvis_brain
    from jarvis.core.settings_store import settings
    from jarvis.state import AgentState, broadcaster
    from jarvis.voice.voice_manager import voice_manager

    monkeypatch.setattr(jarvis_brain, "_provider_override", DeleteProposingProvider())

    def fake_speak(text):
        if not text:
            return
        broadcaster.set(AgentState.SPEAKING)
        broadcaster.set(AgentState.IDLE)

    monkeypatch.setattr(voice_manager, "speak", fake_speak)
    settings.set("confirm.timeout_s", 5, persist=False)  # bound any test hang
    jarvis_brain.reset()
    files.AGENT_FILES_DIR.mkdir(parents=True, exist_ok=True)
    target = files.AGENT_FILES_DIR / "ws-test.txt"
    target.write_text("ws", encoding="utf-8")
    with TestClient(server.app) as c:
        yield c, target
    settings.set("confirm.timeout_s", 30, persist=False)
    target.unlink(missing_ok=True)


def test_ws_confirm_round_trip_on_one_socket(confirm_client):
    """THE deadlock test: the confirm response must be readable on the same
    socket that triggered the blocking action."""
    client, target = confirm_client
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # hello
        ws.send_json({"type": "chat", "text": "delete ws-test.txt"})

        confirm = None
        for _ in range(30):
            event = ws.receive_json()
            if event["type"] == "confirm_request":
                confirm = event
                break
        assert confirm, "confirm_request never arrived"
        assert "ws-test.txt" in confirm["description"]

        ws.send_json({"type": "confirm_response", "id": confirm["id"],
                      "approved": True})
        events = _drain_until_idle_after_speaking(ws)

    assert not target.exists(), "approved deletion must happen"
    resolved = [e for e in events if e.get("type") == "confirm_resolved"]
    assert resolved and resolved[0]["result"] == "approved"
    reply = next(e["text"] for e in events
                 if e.get("type") == "transcript" and e["who"] == "jarvis")
    assert "Deleted" in reply


def test_ws_confirm_decline_leaves_file(confirm_client):
    client, target = confirm_client
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "chat", "text": "delete ws-test.txt"})
        confirm = None
        for _ in range(30):
            event = ws.receive_json()
            if event["type"] == "confirm_request":
                confirm = event
                break
        ws.send_json({"type": "confirm_response", "id": confirm["id"],
                      "approved": False})
        events = _drain_until_idle_after_speaking(ws)

    assert target.exists(), "declined deletion must not happen"
    reply = next(e["text"] for e in events
                 if e.get("type") == "transcript" and e["who"] == "jarvis")
    assert "CANCELLED" in reply


def test_second_chat_mid_confirm_gets_busy(confirm_client):
    client, target = confirm_client
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "chat", "text": "delete ws-test.txt"})
        confirm = None
        busy_seen = False
        for _ in range(30):
            event = ws.receive_json()
            if event["type"] == "confirm_request":
                confirm = event
                ws.send_json({"type": "chat", "text": "and also do this"})
            if event.get("type") == "error" and event.get("message") == "busy":
                busy_seen = True
                break
        assert confirm and busy_seen
        ws.send_json({"type": "confirm_response", "id": confirm["id"],
                      "approved": False})
        _drain_until_idle_after_speaking(ws)


def test_pending_confirm_replayed_to_new_connection(confirm_client):
    client, _ = confirm_client
    result = {}

    def blocked_request():
        result["decision"] = confirmations.request("replay me", timeout_s=5)

    t = threading.Thread(target=blocked_request)
    t.start()
    deadline = time.time() + 2
    while time.time() < deadline and confirmations.pending_event() is None:
        time.sleep(0.02)
    try:
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "state"
            replay = ws.receive_json()
            assert replay["type"] == "confirm_request"
            assert replay["description"] == "replay me"
            ws.send_json({"type": "confirm_response", "id": replay["id"],
                          "approved": False})
    finally:
        t.join()
    assert result["decision"].approved is False


def test_ws_connect_replays_chain_snapshot(client):
    """Slice 6: a HUD (re)connecting mid-chain must receive the chain state
    so the strip re-renders — mirrors the confirm-modal replay."""
    import time
    from jarvis.core import chain

    tracker = chain.start()
    try:
        tracker.set_plan(["open notepad", "type the note"])
        n = tracker.begin_call("launch_app", {"name": "notepad"})
        tracker.end_call(n, "ok")
        time.sleep(0.1)  # let the fanout drain the pre-connect events
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "state"
            # Bound the wait: trigger a chat so a MISSING snapshot fails at
            # the first chat-flow event instead of hanging receive_json()
            # forever (the snapshot, when present, precedes chat events —
            # it's sent in the connect handshake).
            ws.send_json({"type": "chat", "text": "status report"})
            snap = None
            for _ in range(30):
                e = ws.receive_json()
                if e.get("type") == "chain":
                    snap = e
                    break
                if e.get("type") == "transcript":
                    break  # chat flow began — the handshake had no snapshot
            assert snap is not None, "no chain snapshot in the WS handshake"
            assert snap["steps"] == ["open notepad", "type the note"]
            assert snap["cursor"] == 1
            assert snap["aborted"] is None
    finally:
        chain.clear("done")


def test_ws_connect_no_chain_no_snapshot(client):
    """No live chain -> connect handshake is just the state sync."""
    from jarvis.core import chain
    assert chain.current() is None
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "state"
        ws.send_json({"type": "chat", "text": "status report"})
        nxt = ws.receive_json()  # first event of the chat flow, not a chain snap
        assert nxt["type"] != "chain"


def test_huge_chat_is_truncated_not_fatal(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "chat", "text": "x" * 100_000})
        events = _drain_until_idle_after_speaking(ws)
        user_lines = [e["text"] for e in events
                      if e["type"] == "transcript" and e["who"] == "user"]
        assert len(user_lines) == 1 and len(user_lines[0]) <= 4000
