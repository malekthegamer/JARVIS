"""Wake-word listener (slice 13) — "hey Jarvis" via openWakeWord.

An ALTERNATIVE trigger to push-to-talk; neither disables the other. This module
only decides WHEN a session starts — once it fires, everything flows through the
existing pipeline unchanged.

PRIVACY CONTRACT (the load-bearing guarantee):
  Before the wake word fires, audio is processed LOCALLY ONLY and continuously
  discarded. The pre-trigger loop reads one frame, scores it with the local
  openWakeWord model, and lets that frame go out of scope on the next iteration.
  It NEVER writes audio to disk, NEVER touches the network / STT, and keeps NO
  rolling buffer. The only outward call is `on_wake()`, invoked ONLY after a
  threshold crossing — and STT (the sole network-audio path) lives behind that,
  in the follow-up, exactly like push-to-talk today.

SINGLE MIC OWNER: while listening, this owns the mic. On a detection it CLOSES
its own stream (releasing the device) before running the follow-up + response,
then reopens — so there are never two open input streams contending for frames.

Never raises out to a run loop; a bad frame / model hiccup / lost mic is handled
honestly (skip, back off, or stop with a recorded reason).
"""
from __future__ import annotations

import threading
import time

DEFAULT_MODEL = "hey_jarvis"      # openWakeWord ships this pretrained model
DEFAULT_THRESHOLD = 0.5           # Stage-0 measured: silence 0.000, speech 0.78–0.998
DEFAULT_COOLDOWN_S = 2.0
FRAME = 1280                      # openWakeWord chunk (80 ms @ 16 kHz)
RATE = 16000


class WakeListener:
    """Detect the wake word and call `on_wake()` (synchronously) when it fires.
    `model` and `source` are injectable for tests; built lazily in start()."""

    def __init__(self, on_wake, *, model=None, source=None,
                 threshold: float = DEFAULT_THRESHOLD,
                 cooldown_s: float = DEFAULT_COOLDOWN_S,
                 clock=time.monotonic):
        self._on_wake = on_wake
        self._model = model
        self._source = source
        self._threshold = float(threshold)
        self._cooldown_s = float(cooldown_s)
        self._clock = clock
        self._cooldown_until = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.failed_reason: str | None = None

    # ---- the honesty core: one frame in, fire-or-not out ----
    def _process_frame(self, frame) -> bool:
        """Score one frame. If the wake word crosses threshold (and we're past
        the cooldown), release the mic, fire on_wake, arm the cooldown, reopen.
        Returns whether it fired. Pure of any STT/file work; never raises."""
        try:
            scores = self._model.predict(frame)
        except Exception:
            return False  # a per-frame model hiccup is not a trigger
        score = max(scores.values()) if scores else 0.0
        if score < self._threshold or self._clock() < self._cooldown_until:
            return False
        # single mic owner: release the device BEFORE the follow-up capture
        try:
            self._source.close()
        except Exception:
            pass
        try:
            self._on_wake()
        except Exception:
            pass  # a failing interaction must not kill the listener
        self._cooldown_until = self._clock() + self._cooldown_s
        try:
            self._source.open()  # resume listening
        except Exception:
            self._stop.set()
            self.failed_reason = "lost the mic after a wake"
        return True

    # ---- the run loop (threaded) ----
    def _run(self) -> None:
        try:
            self._source.open()
        except Exception as exc:
            self.failed_reason = f"mic unavailable: {exc}"
            return
        while not self._stop.is_set():
            try:
                frame = self._source.read_frame()
            except Exception:
                if self._stop.wait(0.2):  # transient read error: brief backoff
                    break
                continue
            if frame is None:
                continue
            self._process_frame(frame)
        try:
            self._source.close()
        except Exception:
            pass

    def start(self) -> None:
        if self._model is None:
            self._model = _build_model()
        if self._source is None:
            self._source = _MicSource()
        self._stop.clear()
        self.failed_reason = None
        self._thread = threading.Thread(target=self._run, name="jarvis-wake",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=3.0)
        self._thread = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


def handle_wake(listen, respond, set_idle, timeout_s: float = 5.0):
    """Run after the wake word fires: capture ONE follow-up utterance. A real
    utterance runs respond(text); silence/empty returns to IDLE quietly — a
    false trigger never acts on noise. Returns the text acted on, or None.
    Never raises."""
    try:
        text = listen(timeout_s)
    except Exception:
        text = None
    if not text or not str(text).strip():
        try:
            set_idle()
        except Exception:
            pass
        return None
    text = str(text).strip()
    try:
        respond(text)
    except Exception:
        pass
    return text


# ---- real dependencies (injected-over in tests; proven live in Stage 0/3) ----

def _build_model():
    """The local openWakeWord model. Import is lazy so the deterministic suite
    and headless paths never require the package."""
    from openwakeword.model import Model
    from jarvis.core.settings_store import settings
    name = settings.get("wake.model", DEFAULT_MODEL)
    return Model(wakeword_models=[name], inference_framework="onnx")


class _MicSource:
    """16 kHz / 1280-sample mono int16 frames from the same real mic push-to-talk
    uses (capture.find_real_mic). Stage 0 proved the Realtek mic opens directly
    at 16 kHz; if a device refuses, fall back to its default rate + resample."""

    def __init__(self):
        self._pa = None
        self._stream = None
        self._resample_from = None
        self._resample_state = None

    def open(self):
        import pyaudio
        from jarvis.voice import capture
        idx, _name = capture.find_real_mic()
        self._pa = pyaudio.PyAudio()
        try:
            self._stream = self._pa.open(
                rate=RATE, channels=1, format=pyaudio.paInt16, input=True,
                input_device_index=idx, frames_per_buffer=FRAME)
            self._resample_from = None
        except Exception:
            # device won't do 16 kHz — open at its default and resample
            info = (self._pa.get_device_info_by_index(idx) if idx is not None
                    else self._pa.get_default_input_device_info())
            dev_rate = int(info["defaultSampleRate"])
            in_frames = max(FRAME, int(FRAME * dev_rate / RATE) + 8)
            self._stream = self._pa.open(
                rate=dev_rate, channels=1, format=pyaudio.paInt16, input=True,
                input_device_index=idx, frames_per_buffer=in_frames)
            self._resample_from = dev_rate
            self._resample_state = None
            self._in_frames = in_frames

    def read_frame(self):
        import numpy as np
        if self._resample_from is None:
            raw = self._stream.read(FRAME, exception_on_overflow=False)
        else:
            from jarvis.voice.capture import require_audio_stdlib
            require_audio_stdlib()
            import audioop
            src = self._stream.read(self._in_frames, exception_on_overflow=False)
            raw, self._resample_state = audioop.ratecv(
                src, 2, 1, self._resample_from, RATE, self._resample_state)
        return np.frombuffer(raw, dtype=np.int16)

    def close(self):
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        finally:
            self._stream = None
            if self._pa is not None:
                self._pa.terminate()
                self._pa = None
