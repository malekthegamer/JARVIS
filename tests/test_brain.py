"""Stage-2 exit tests: text -> brain -> text, with the state machine and the
never-crash contract. (a) fake provider drives the THINKING->IDLE sequence;
(b) live Gemini ping; (c) a timeout-raising provider still restores IDLE and
returns a friendly message, never an exception."""
from __future__ import annotations

import pytest

from jarvis.core.errors import ProviderError
from jarvis.providers.brain.base import BrainProvider, BrainResponse
from jarvis.state import AgentState, broadcaster


class FakeProvider(BrainProvider):
    supports_tools = True

    def __init__(self, reply="All systems nominal, sir."):
        self.reply = reply
        self.calls: list[dict] = []

    def is_configured(self):
        return True

    def generate(self, messages, system_prompt, tools=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        return BrainResponse(text=self.reply)


class TimeoutProvider(BrainProvider):
    def is_configured(self):
        return True

    def generate(self, messages, system_prompt, tools=None):
        raise ProviderError("connection", "Gemini", "request timed out after 30s")


@pytest.fixture()
def state_log():
    events: list[dict] = []
    unsubscribe = broadcaster.subscribe(events.append)
    yield events
    unsubscribe()


def _make_brain(provider):
    from jarvis.brain import JarvisBrain
    brain = JarvisBrain()
    brain._provider_override = provider
    return brain


def test_fake_provider_reply_and_state_sequence(state_log):
    fake = FakeProvider()
    brain = _make_brain(fake)
    reply = brain.think("status report")

    assert reply == "All systems nominal, sir."
    states = [e["state"] for e in state_log]
    assert states == [AgentState.THINKING.value, AgentState.IDLE.value]
    assert broadcaster.current is AgentState.IDLE
    # history holds the full exchange
    assert brain.history[-2:] == [
        {"role": "user", "content": "status report"},
        {"role": "assistant", "content": "All systems nominal, sir."},
    ]
    # skeleton registers no tools -> provider must receive tools=None
    assert fake.calls[0]["tools"] is None


def test_timeout_returns_friendly_and_restores_idle(state_log):
    brain = _make_brain(TimeoutProvider())
    reply = brain.think("hello?")

    assert "Couldn't reach Gemini" in reply  # friendly text, not a traceback
    assert broadcaster.current is AgentState.IDLE
    assert state_log[-1]["state"] == AgentState.IDLE.value


def test_unexpected_exception_never_escapes(state_log):
    class ExplodingProvider(BrainProvider):
        def is_configured(self):
            return True

        def generate(self, messages, system_prompt, tools=None):
            raise RuntimeError("kaboom")

    brain = _make_brain(ExplodingProvider())
    reply = brain.think("hi")
    assert "kaboom" in reply or "wrong" in reply.lower()
    assert broadcaster.current is AgentState.IDLE


def test_history_trims_to_limit():
    fake = FakeProvider(reply="ok")
    brain = _make_brain(fake)
    for i in range(60):
        brain.think(f"message {i}")
    from jarvis.core.settings_store import settings
    limit = int(settings.get("history_max_messages", 40))
    assert len(brain.history) <= limit
    assert brain.history[0]["role"] == "user"  # never starts mid-exchange


def test_live_gemini_ping():
    """The real thing: one live round-trip through the actual provider."""
    from jarvis import config
    if not config.get_api_key("gemini"):
        pytest.skip("GEMINI_API_KEY not configured")
    from jarvis.brain import JarvisBrain
    brain = JarvisBrain()
    reply = brain.think("What is 2+2? Reply with just the number.")
    assert reply, "empty reply from live Gemini"
    assert "isn't configured" not in reply and "hit a problem" not in reply, reply
    assert "4" in reply, reply
