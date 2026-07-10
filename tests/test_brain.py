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
    # slice 2+: the primitive schemas reach the provider
    tool_names = [t["name"] for t in fake.calls[0]["tools"]]
    assert "launch_app" in tool_names


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


# ---------- slice 10: long-term memory integration ----------

from jarvis.providers.brain.base import ToolCall


class CapturingProvider(BrainProvider):
    """Records the system_prompt it was handed; returns plain text."""
    supports_tools = True

    def __init__(self, reply="Noted, sir."):
        self.reply = reply
        self.system_prompts: list[str] = []

    def is_configured(self):
        return True

    def generate(self, messages, system_prompt, tools=None):
        self.system_prompts.append(system_prompt)
        return BrainResponse(text=self.reply)


class ScriptedToolProvider(BrainProvider):
    """Round 1: emit the given tool call. Round 2: final prose."""
    supports_tools = True

    def __init__(self, name, args):
        self.tc = ToolCall(id="m1", name=name, args=args)
        self.rounds = 0
        self.tool_results: list[str] = []

    def is_configured(self):
        return True

    def generate(self, messages, system_prompt, tools=None):
        self.rounds += 1
        if self.rounds == 1:
            return BrainResponse(tool_calls=[self.tc])
        self.tool_results = [m["content"] for m in messages if m["role"] == "tool"]
        return BrainResponse(text="Done, sir.")


@pytest.fixture()
def mem_brain(tmp_path):
    from jarvis.core.memory import MemoryStore
    brain = _make_brain(CapturingProvider())
    brain.memory = MemoryStore(tmp_path / "mem.bin")
    return brain


def test_relevant_memory_injected_into_system_prompt(mem_brain):
    mem_brain.memory.add("I am allergic to peanuts")
    mem_brain.think("what am I allergic to?")
    prompt = mem_brain.provider().system_prompts[-1]
    assert "peanuts" in prompt
    assert "REMEMBER ABOUT THE USER" in prompt


def test_unrelated_query_no_memory_block(mem_brain):
    mem_brain.memory.add("I am allergic to peanuts")
    mem_brain.think("what is the capital of France?")
    prompt = mem_brain.provider().system_prompts[-1]
    assert "peanuts" not in prompt
    assert "REMEMBER ABOUT THE USER" not in prompt


def test_remember_tool_persists(tmp_path):
    from jarvis.core.memory import MemoryStore
    store = MemoryStore(tmp_path / "mem.bin")
    brain = _make_brain(ScriptedToolProvider("remember",
                                             {"text": "I take my coffee black"}))
    brain.memory = store
    brain.think("remember that I take my coffee black")
    assert any("coffee black" in r["text"] for r in store.all())
    # and it survives a restart (fresh instance, same file)
    assert any("coffee black" in r["text"]
               for r in MemoryStore(tmp_path / "mem.bin").all())


def test_recall_tool_lists(tmp_path):
    from jarvis.core.memory import MemoryStore
    store = MemoryStore(tmp_path / "mem.bin")
    store.add("I am allergic to peanuts")
    store.add("my car is a blue Civic")
    brain = _make_brain(ScriptedToolProvider("recall", {}))
    brain.memory = store
    brain.think("what do you remember about me?")
    listing = " ".join(brain.provider().tool_results)
    assert "peanuts" in listing and "Civic" in listing


def test_forget_tool_removes_with_readback(tmp_path):
    from jarvis.core.memory import MemoryStore
    store = MemoryStore(tmp_path / "mem.bin")
    store.add("I am allergic to peanuts")
    store.add("my flight is on Tuesday")
    brain = _make_brain(ScriptedToolProvider("forget", {"query": "peanuts"}))
    brain.memory = store
    brain.think("forget that I'm allergic to peanuts")
    assert not any("peanuts" in r["text"] for r in store.all())
    assert any("peanuts" in tr for tr in brain.provider().tool_results)  # readback


def test_forget_tool_ambiguous_deletes_nothing(tmp_path):
    from jarvis.core.memory import MemoryStore
    store = MemoryStore(tmp_path / "mem.bin")
    store.add("I take my coffee black")
    store.add("I drink my coffee at 8am")
    brain = _make_brain(ScriptedToolProvider("forget", {"query": "coffee"}))
    brain.memory = store
    brain.think("forget my coffee thing")
    assert len(store.all()) == 2, "ambiguous forget must delete nothing"
    result = " ".join(brain.provider().tool_results).lower()
    assert "which" in result or "match" in result  # asks the user to disambiguate


def test_memory_error_does_not_break_think(mem_brain, monkeypatch):
    """A memory-subsystem failure must never break the reply loop."""
    monkeypatch.setattr(mem_brain.memory, "retrieve",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    reply = mem_brain.think("hello there")
    assert reply and "boom" not in reply  # degraded to no-memory, still answered


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
