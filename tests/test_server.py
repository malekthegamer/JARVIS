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


def test_huge_chat_is_truncated_not_fatal(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "chat", "text": "x" * 100_000})
        events = _drain_until_idle_after_speaking(ws)
        user_lines = [e["text"] for e in events
                      if e["type"] == "transcript" and e["who"] == "user"]
        assert len(user_lines) == 1 and len(user_lines[0]) <= 4000
