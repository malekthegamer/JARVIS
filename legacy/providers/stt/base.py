"""STTProvider interface.

`audio` is a speech_recognition.AudioData produced by the ONE shared capture
routine in voice/capture.py — providers never touch the microphone themselves.
"""
from __future__ import annotations


class STTProvider:
    name: str = "base"

    def transcribe(self, audio) -> str | None:
        """Return the transcript, or None if speech was unintelligible.
        Raise ProviderError on provider failure (caller loops back to listening)."""
        raise NotImplementedError

    def is_configured(self) -> bool:
        raise NotImplementedError
