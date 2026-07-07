"""ElevenLabs — best quality TTS. Requires an API key; free tier has a monthly
character quota. Quota-out or missing key degrade to a clear message (the
voice_manager then falls back to edge-tts), never a crash."""
from __future__ import annotations

import config
from core.errors import ProviderError, classify_exception
from core.settings_store import settings
from providers.registry import register
from providers.tts.base import TTSProvider

DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # "Rachel" — a safe stock voice


@register("tts", "elevenlabs")
class ElevenLabsProvider(TTSProvider):
    def is_configured(self) -> bool:
        return config.get_api_key("elevenlabs") is not None

    def _client(self):
        from elevenlabs.client import ElevenLabs  # lazy
        key = config.get_api_key("elevenlabs")
        if not key:
            raise ProviderError("missing_key", "ElevenLabs")
        return ElevenLabs(api_key=key)

    def synthesize(self, text: str) -> bytes | None:
        voice_id = settings.get("tts.elevenlabs_voice_id") or DEFAULT_VOICE_ID
        try:
            audio = self._client().text_to_speech.convert(
                voice_id=voice_id, text=text,
                model_id="eleven_multilingual_v2", output_format="mp3_44100_128",
            )
            return b"".join(audio)
        except ProviderError:
            raise
        except Exception as exc:
            raise classify_exception(exc, "ElevenLabs")

    def speak(self, text: str) -> None:
        data = self.synthesize(text)
        if data:
            from voice import playback
            playback.play_bytes(data)

    def list_voices(self) -> list[dict]:
        """Available voices for the settings picker (needs a key)."""
        try:
            resp = self._client().voices.get_all()
            return [{"id": v.voice_id, "label": v.name} for v in resp.voices]
        except Exception:
            return []

    def quota(self) -> dict | None:
        """Remaining character quota, surfaced in the settings page."""
        try:
            sub = self._client().user.subscription.get()
            return {"used": sub.character_count, "limit": sub.character_limit}
        except Exception:
            return None
