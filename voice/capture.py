"""ONE shared, provider-agnostic microphone capture routine (PyAudio via
speech_recognition). Every STT provider receives AudioData from here — no
per-provider mic code anywhere.

Hard-won lessons encoded here (do not undo):
- NEVER force a sample rate (16 kHz on the Realtek mic -> PaErrorCode -9984).
  Let PyAudio use the device default.
- NEVER request exclusive-mode audio; plain PyAudio shares the mic with
  Discord etc. by default.
- Voicemeeter floods the device list with virtual devices; auto-detect the
  real mic by name and let settings override the index (it shifts when
  devices are re-plugged — never hardcode).
- Timeouts/failures return None and the caller loops back to listening;
  nothing here ever raises out to a run loop.
"""
from __future__ import annotations

import threading

from core.settings_store import settings

REAL_MIC_HINTS = ("microphone", "realtek", "headset")
VIRTUAL_HINTS = (
    "voicemeeter", "vb-audio", "cable", "virtual", "stereo mix", "loopback",
    "audiorelay", "steam streaming", "wave link", "sound mapper", "primary sound",
    "output", "speakers", "wo mic", "voicemod",  # phone-mic / voice-changer apps
)

_lock = threading.Lock()
_recognizer = None
_calibrated_index: int | None = None


def is_probably_real_mic(name: str) -> bool:
    lower = name.lower()
    return any(h in lower for h in REAL_MIC_HINTS) and not any(h in lower for h in VIRTUAL_HINTS)


def list_input_devices() -> list[tuple[int, str]]:
    import speech_recognition as sr  # lazy
    return list(enumerate(sr.Microphone.list_microphone_names()))


def find_real_mic() -> tuple[int | None, str]:
    """Return (device_index, name). Settings override wins; else first device
    passing the real-mic filter; else system default (None index)."""
    override = settings.get("stt.mic_device_index")
    devices = list_input_devices()
    if override is not None:
        for idx, name in devices:
            if idx == int(override):
                return idx, name
    # Rank candidates: a Realtek/hardware mic beats a generic "Microphone (...)"
    # name — virtual mic apps (WO Mic, Voicemod) love the generic label.
    ranked: list[tuple[int, int, str]] = []
    for idx, name in devices:
        if not is_probably_real_mic(name):
            continue
        lower = name.lower()
        rank = 0 if "realtek" in lower else (1 if "headset" in lower else 2)
        ranked.append((rank, idx, name))
    if ranked:
        _rank, idx, name = min(ranked)
        return idx, name
    return None, "system default"


def listen_once(timeout: float = 8.0, phrase_time_limit: float = 15.0):
    """Capture one utterance. Returns sr.AudioData or None (timeout / device issue)."""
    global _recognizer, _calibrated_index
    import speech_recognition as sr  # lazy

    with _lock:
        if _recognizer is None:
            _recognizer = sr.Recognizer()
            _recognizer.dynamic_energy_threshold = True
            _recognizer.pause_threshold = 0.8

        index, _name = find_real_mic()
        try:
            # NOTE: no sample_rate argument — device default only (lesson #1).
            with sr.Microphone(device_index=index) as source:
                if _calibrated_index != index:
                    _recognizer.adjust_for_ambient_noise(source, duration=0.6)
                    _calibrated_index = index
                return _recognizer.listen(source, timeout=timeout,
                                          phrase_time_limit=phrase_time_limit)
        except sr.WaitTimeoutError:
            return None  # silence — caller just listens again
        except OSError as exc:
            print(f"  [mic] device problem ({exc}) — re-detecting on next attempt")
            _calibrated_index = None
            return None
        except Exception as exc:
            print(f"  [mic] capture failed ({exc}) — retrying")
            return None
