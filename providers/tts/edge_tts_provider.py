"""edge-tts — free natural neural voices. Needs internet, no API key.
The effective first-run default voice (until an ElevenLabs key is saved)."""
from __future__ import annotations

import asyncio

from core.errors import ProviderError, classify_exception
from core.settings_store import settings
from providers.registry import register
from providers.tts.base import TTSProvider


@register("tts", "edge_tts")
class EdgeTTSProvider(TTSProvider):
    def is_configured(self) -> bool:
        return True  # no key; failures surface as connection errors at call time

    def synthesize(self, text: str) -> bytes | None:
        try:
            import edge_tts  # lazy
        except ImportError as exc:
            raise ProviderError("generic", "edge-tts", f"not installed: {exc}")

        voice = settings.get("tts.edge_voice", "en-GB-RyanNeural")

        async def _collect() -> bytes:
            communicate = edge_tts.Communicate(text, voice)
            chunks = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            return b"".join(chunks)

        try:
            data = asyncio.run(_collect())
        except Exception as exc:
            raise classify_exception(exc, "edge-tts")
        if not data:
            raise ProviderError("bad_response", "edge-tts", "no audio returned")
        return data

    def speak(self, text: str) -> None:
        data = self.synthesize(text)
        if data:
            from voice import playback
            playback.play_bytes(data)

    @staticmethod
    def list_voices() -> list[dict]:
        """English neural voices for the settings picker."""
        try:
            import edge_tts
            voices = asyncio.run(edge_tts.list_voices())
            return [{"id": v["ShortName"], "label": f'{v["ShortName"]} ({v["Gender"]})'}
                    for v in voices if v["Locale"].startswith("en-")]
        except Exception:
            return []
