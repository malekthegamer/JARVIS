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

import sys
import threading

from jarvis.core.settings_store import settings

# --- the Python 3.12 contract -------------------------------------------------
# Python 3.13 REMOVED the stdlib `audioop` and `aifc` modules (PEP 594).
# SpeechRecognition imports both unguarded at module level, and wake.py uses
# audioop.ratecv to resample the mic — so on 3.13+ every voice path dies with a
# bare "No module named 'audioop'", naming a stdlib module the user has never
# heard of and giving no hint that the interpreter is the problem. pip installs
# cleanly there (PyAudio ships a cp313 wheel), so nothing earlier complains.
# install.bat pins 3.12; this is the belt-and-braces for a hand-made venv.
_AUDIO_STDLIB = ("audioop", "aifc")
_audio_stdlib_ok: bool | None = None


def unsupported_python_message(missing: str) -> str:
    return (
        f"Voice needs Python 3.12 — this interpreter is Python "
        f"{sys.version_info.major}.{sys.version_info.minor}, where the standard "
        f"library '{missing}' module no longer exists (removed in Python 3.13, "
        f"PEP 594). SpeechRecognition and the wake-word resampler both require "
        f"it, so microphone input cannot work here. Re-run install.bat — it "
        f"installs and pins Python 3.12."
    )


def require_audio_stdlib() -> None:
    """Raise a diagnosable error instead of a raw ModuleNotFoundError.

    Result is cached: this sits on the per-frame wake-word path, so it must be
    free after the first call."""
    global _audio_stdlib_ok
    if _audio_stdlib_ok:
        return
    import importlib.util
    for mod in _AUDIO_STDLIB:
        if importlib.util.find_spec(mod) is None:
            raise RuntimeError(unsupported_python_message(mod))
    _audio_stdlib_ok = True

REAL_MIC_HINTS = ("microphone", "realtek", "headset")
VIRTUAL_HINTS = (
    # "voicemeet" not "voicemeeter": MME truncates device names to 31 chars,
    # so "Virtual Mix (VB-Audio Voicemeet" must still match.
    "voicemeet", "vb-audio", "cable", "virtual", "stereo mix", "loopback",
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
    require_audio_stdlib()
    import speech_recognition as sr  # lazy
    return list(enumerate(sr.Microphone.list_microphone_names()))


def find_real_mic() -> tuple[int | None, str]:
    """Return (device_index, name).

    Windows device indices SHIFT when audio devices appear/disappear, so a
    pinned index alone can silently start pointing at a Voicemeeter virtual
    cable (this exact failure happened). Resolution order:
      1. pinned index, but ONLY if its current name still matches the pinned
         name (or passes the real-mic filter when no name was stored);
      2. the pinned NAME found at whatever index it moved to;
      3. ranked auto-detect;
      4. system default.
    """
    override = settings.get("stt.mic_device_index")
    pinned_name = (settings.get("stt.mic_device_name") or "").strip()
    devices = list_input_devices()
    if override is not None:
        for idx, name in devices:
            if idx != int(override):
                continue
            if pinned_name and name.strip() == pinned_name:
                return idx, name  # pin still valid
            if not pinned_name and is_probably_real_mic(name):
                return idx, name  # legacy pin, still looks like a real mic
            break  # index exists but points at a different/virtual device now
    if pinned_name:  # indices shifted — chase the device by name
        for idx, name in devices:
            if name.strip() == pinned_name:
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
    require_audio_stdlib()
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
