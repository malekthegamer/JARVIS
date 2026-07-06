"""TTSProvider interface."""
from __future__ import annotations


class TTSProvider:
    name: str = "base"

    def speak(self, text: str) -> None:
        """Synthesize and play locally (blocking). Raise ProviderError on failure."""
        raise NotImplementedError

    def synthesize(self, text: str) -> bytes | None:
        """Return audio bytes (mp3/wav) for the web dashboard, or None if the
        provider can only play locally."""
        return None

    def is_configured(self) -> bool:
        raise NotImplementedError
