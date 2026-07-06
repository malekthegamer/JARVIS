"""pyttsx3 — fully offline TTS. Robotic but has zero network dependency;
the last-resort fallback that always works."""
from __future__ import annotations

from core.errors import classify_exception
from core.settings_store import settings
from providers.registry import register
from providers.tts.base import TTSProvider


@register("tts", "pyttsx3")
class Pyttsx3Provider(TTSProvider):
    def is_configured(self) -> bool:
        return True

    def speak(self, text: str) -> None:
        try:
            import pyttsx3  # lazy
            # Fresh engine per call: the shared-engine runAndWait loop is known
            # to wedge after repeated calls on Windows.
            engine = pyttsx3.init()
            engine.setProperty("rate", int(settings.get("tts.pyttsx3_rate", 180)))
            engine.say(text)
            engine.runAndWait()
            engine.stop()
        except Exception as exc:
            raise classify_exception(exc, "pyttsx3")

    def synthesize(self, text: str) -> bytes | None:
        """Render to a temp WAV for the dashboard."""
        import tempfile
        from pathlib import Path
        try:
            import pyttsx3
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "speech.wav"
                engine = pyttsx3.init()
                engine.setProperty("rate", int(settings.get("tts.pyttsx3_rate", 180)))
                engine.save_to_file(text, str(path))
                engine.runAndWait()
                engine.stop()
                return path.read_bytes() if path.exists() else None
        except Exception as exc:
            raise classify_exception(exc, "pyttsx3")
