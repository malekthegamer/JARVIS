"""Stage-3 exit tests: edge-tts produces real audio bytes, pyttsx3 works
offline, the fallback chain walks active -> edge -> pyttsx3 under forced
failure, and speak() emits SPEAKING then restores IDLE."""
from __future__ import annotations

import pytest

from jarvis.core.errors import ProviderError
from jarvis.state import AgentState, broadcaster
from jarvis.voice.voice_manager import VoiceManager


class FakeTTS:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail
        self.spoken: list[str] = []

    def is_configured(self):
        return True

    def speak(self, text):
        if self.fail:
            raise ProviderError("connection", self.name, "forced failure")
        self.spoken.append(text)

    def synthesize(self, text):
        if self.fail:
            raise ProviderError("connection", self.name, "forced failure")
        return b"fake-audio"


@pytest.fixture()
def state_log():
    events: list[dict] = []
    unsubscribe = broadcaster.subscribe(events.append)
    yield events
    unsubscribe()


def _patch_registry(monkeypatch, providers: dict):
    from jarvis.voice import voice_manager as vm_module

    def fake_get(family, name):
        assert family == "tts"
        return providers.get(name)

    monkeypatch.setattr(vm_module.registry, "get", fake_get)


def test_speak_uses_active_provider(monkeypatch, state_log):
    edge, py = FakeTTS("edge_tts"), FakeTTS("pyttsx3")
    _patch_registry(monkeypatch, {"edge_tts": edge, "pyttsx3": py})
    vm = VoiceManager()
    vm.speak("good evening, sir")

    assert edge.spoken == ["good evening, sir"]
    assert py.spoken == []
    states = [e["state"] for e in state_log]
    assert states == [AgentState.SPEAKING.value, AgentState.IDLE.value]


def test_fallback_reaches_pyttsx3_when_edge_fails(monkeypatch, state_log):
    edge, py = FakeTTS("edge_tts", fail=True), FakeTTS("pyttsx3")
    _patch_registry(monkeypatch, {"edge_tts": edge, "pyttsx3": py})
    vm = VoiceManager()
    vm.speak("fallback test")

    assert py.spoken == ["fallback test"]
    assert broadcaster.current is AgentState.IDLE


def test_all_providers_failing_never_raises_and_restores_idle(monkeypatch, state_log):
    _patch_registry(monkeypatch, {"edge_tts": FakeTTS("e", fail=True),
                                  "pyttsx3": FakeTTS("p", fail=True)})
    vm = VoiceManager()
    vm.speak("into the void")  # must not raise
    assert broadcaster.current is AgentState.IDLE
    assert state_log[-1]["state"] == AgentState.IDLE.value


def test_empty_text_is_a_silent_noop(monkeypatch, state_log):
    _patch_registry(monkeypatch, {})
    vm = VoiceManager()
    vm.speak("")
    assert state_log == []  # no state churn for nothing to say


def test_edge_tts_synthesizes_real_audio_bytes():
    """Live network test: edge-tts must return non-trivial audio."""
    from jarvis.providers import registry
    edge = registry.get("tts", "edge_tts")
    data = edge.synthesize("Systems online.")
    assert data and len(data) > 1000, f"suspiciously small audio: {len(data or b'')} bytes"


def test_pyttsx3_synthesizes_offline():
    from jarvis.providers import registry
    py = registry.get("tts", "pyttsx3")
    data = py.synthesize("Offline voice check.")
    assert data and len(data) > 1000
