"""Local Whisper via faster-whisper — offline, private, GPU-accelerated.

Blackwell (RTX 50-series / sm_120) note: int8 kernels crash with
CUBLAS_STATUS_NOT_SUPPORTED on these cards, so CUDA runs use float16.
If CUDA libraries fail to load at all, we fall back to CPU int8 and keep
working — a dashboard-visible note, never a crash.
"""
from __future__ import annotations

import io
import threading

from core.errors import ProviderError
from core.settings_store import settings
from providers.registry import register
from providers.stt.base import STTProvider


@register("stt", "local_whisper")
class LocalWhisperProvider(STTProvider):
    def __init__(self) -> None:
        self._model = None
        self._model_key: tuple | None = None
        self._lock = threading.Lock()
        self.active_device: str | None = None  # "cuda" | "cpu" after first load

    def is_configured(self) -> bool:
        # find_spec instead of importing — the real import costs seconds and
        # would stall the settings page just to answer "is it installed?".
        import importlib.util
        return importlib.util.find_spec("faster_whisper") is not None

    def _get_model(self):
        from faster_whisper import WhisperModel  # lazy

        size = settings.get("stt.whisper_model", "medium")
        device = settings.get("stt.whisper_device", "cuda")
        key = (size, device)
        with self._lock:
            if self._model is not None and self._model_key == key:
                return self._model
            self._model = None  # release old model (VRAM) before loading new
            if device == "cuda":
                try:
                    # float16 REQUIRED on Blackwell/sm_120 (int8 -> CUBLAS crash)
                    self._model = WhisperModel(size, device="cuda", compute_type="float16")
                    self.active_device = "cuda"
                except Exception:
                    self._model = None  # CUDA libs missing/broken -> CPU fallback
            if self._model is None:
                self._model = WhisperModel(size, device="cpu", compute_type="int8")
                self.active_device = "cpu"
            self._model_key = key
            return self._model

    def transcribe(self, audio) -> str | None:
        try:
            model = self._get_model()
        except Exception as exc:
            raise ProviderError("generic", "Local Whisper", f"model load failed: {exc}")
        try:
            wav = io.BytesIO(audio.get_wav_data())
            segments, _info = model.transcribe(wav, beam_size=5, vad_filter=True)
            text = " ".join(seg.text.strip() for seg in segments).strip()
            return text or None
        except Exception as exc:
            raise ProviderError("generic", "Local Whisper", str(exc))
