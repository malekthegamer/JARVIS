"""Google Web Speech STT via speech_recognition — free, no key, proven.
The first-run default."""
from __future__ import annotations

from jarvis.core.errors import ProviderError
from jarvis.providers.registry import register
from jarvis.providers.stt.base import STTProvider


@register("stt", "google")
class GoogleSTTProvider(STTProvider):
    def is_configured(self) -> bool:
        return True

    def transcribe(self, audio) -> str | None:
        import speech_recognition as sr  # lazy
        recognizer = sr.Recognizer()
        try:
            return recognizer.recognize_google(audio)
        except sr.UnknownValueError:
            return None  # unintelligible — not an error, just loop back
        except sr.RequestError as exc:
            raise ProviderError("connection", "Google Web Speech", str(exc))
        except Exception as exc:
            raise ProviderError("generic", "Google Web Speech", str(exc))
