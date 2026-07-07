"""OpenAI Whisper API STT — cloud transcription, reuses OPENAI_API_KEY."""
from __future__ import annotations

import config
from core.errors import ProviderError, classify_exception
from providers.registry import register
from providers.stt.base import STTProvider


@register("stt", "openai_whisper")
class OpenAIWhisperProvider(STTProvider):
    def is_configured(self) -> bool:
        return config.get_api_key("openai_whisper") is not None

    def transcribe(self, audio) -> str | None:
        from openai import OpenAI  # lazy
        key = config.get_api_key("openai_whisper")
        if not key:
            raise ProviderError("missing_key", "OpenAI Whisper")
        try:
            client = OpenAI(api_key=key)
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=("speech.wav", audio.get_wav_data()),
            )
            text = (result.text or "").strip()
            return text or None
        except Exception as exc:
            raise classify_exception(exc, "OpenAI Whisper")
