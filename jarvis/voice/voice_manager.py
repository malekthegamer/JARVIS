"""Loads the active STT/TTS providers per settings and exposes listen()/speak().

Callers never know which concrete provider is behind the interface.
Registered as a settings reload listener, so a settings Save & Apply
hot-swaps providers with no restart.

TTS "auto" resolution: preferred provider if configured, else edge-tts, else
pyttsx3 — so the skeleton always has a voice.
"""
from __future__ import annotations

from jarvis.core.errors import ProviderError
from jarvis.core.settings_store import settings
from jarvis.providers import registry
from jarvis.voice import capture


class VoiceManager:
    def __init__(self) -> None:
        settings.on_reload(self._log_swap)

    # ---------- resolution ----------
    def resolve_tts_name(self) -> str:
        active = settings.get("tts.active", "auto")
        if active != "auto":
            return active
        preferred = settings.get("tts.preferred", "edge_tts")
        provider = registry.get("tts", preferred)
        if provider and provider.is_configured():
            return preferred
        edge = registry.get("tts", "edge_tts")
        if edge and edge.is_configured():
            return "edge_tts"
        return "pyttsx3"

    def resolve_stt_name(self) -> str:
        return settings.get("stt.active", "google")

    def _log_swap(self) -> None:
        print(f"  [voice] providers reloaded: stt={self.resolve_stt_name()} "
              f"tts={self.resolve_tts_name()}")

    # ---------- the two calls everyone uses ----------
    def listen(self, timeout: float = 8.0) -> str | None:
        """Capture one utterance and transcribe it. None on silence/failure —
        callers just loop back to listening (never-crash contract)."""
        audio = capture.listen_once(timeout=timeout)
        if audio is None:
            return None
        provider = registry.get("stt", self.resolve_stt_name())
        if provider is None:
            print("  [stt] no STT provider available")
            return None
        try:
            return provider.transcribe(audio)
        except ProviderError as exc:
            print(f"  [stt] {exc.friendly()}")
            return None
        except Exception as exc:
            print(f"  [stt] unexpected: {exc}")
            return None

    def speak(self, text: str) -> None:
        """Speak text via the active TTS, falling back down the free chain
        (active -> edge-tts -> pyttsx3) rather than ever crashing or going mute."""
        if not text:
            return
        tried: list[str] = []
        for name in self._fallback_chain():
            if name in tried:
                continue
            tried.append(name)
            provider = registry.get("tts", name)
            if provider is None:
                continue
            try:
                provider.speak(text)
                return
            except ProviderError as exc:
                print(f"  [tts] {exc.friendly()}")
            except Exception as exc:
                print(f"  [tts] {name} failed: {exc}")
        print("  [tts] all voice output unavailable — text only")

    def synthesize(self, text: str) -> tuple[bytes | None, str]:
        """Audio bytes + the provider name used (for a browser-audio path later)."""
        for name in self._fallback_chain():
            provider = registry.get("tts", name)
            if provider is None:
                continue
            try:
                data = provider.synthesize(text)
                if data:
                    return data, name
            except Exception:
                continue
        return None, ""

    def _fallback_chain(self) -> list[str]:
        return [self.resolve_tts_name(), "edge_tts", "pyttsx3"]


voice_manager = VoiceManager()
