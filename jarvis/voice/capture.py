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
import time
from typing import NamedTuple

from jarvis.core import timing
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
    # A LINE-IN JACK IS NOT A MICROPHONE (slice 58, found by the gate on the
    # owner's real machine). "Line In (Realtek HD Audio Line input)" matched the
    # 'realtek' hint and therefore RANKED FIRST — above his actual earphone mic.
    # Nothing is plugged into that jack, so the stream never opens and voice
    # input silently does not work. Whether it wins depends on device
    # enumeration, so it can flip between runs, which is indistinguishable from
    # "the listening is inconsistent". Anyone genuinely recording from line-in
    # can still pin it with stt.mic_device_index.
    "line in", "line input", "line-in",
)

_lock = threading.Lock()
_recognizer = None
_recognizer_config: tuple | None = None
_calibrated_index: int | None = None
_last_calibrated_at: float = 0.0


# ======================= capture timing (slice 58) =======================
# Reported in real use: "the listening is kind of inconsistent. Sometimes it is
# in listening mode for more than it needs to, and other times it will cut me
# off mid sentence, and it doesn't really understand what I want."
#
# Three separate causes, none of which had a setting or a test before this:
#   * calibration ran ONCE per device for the whole process, while
#     dynamic_energy_threshold drifted from that stale baseline -> the same
#     words behaved differently an hour apart. That is the INCONSISTENCY.
#   * pause_threshold was a hardcoded 0.8s, which clips a mid-thought pause.
#   * phrase_time_limit was a hardcoded 15s that truncates SILENTLY.

class CaptureConfig(NamedTuple):
    pause_threshold: float
    phrase_time_limit: float
    listen_timeout: float
    recalibrate_every_s: float


def capture_config() -> CaptureConfig:
    """Current timing, read fresh every call so a settings change takes effect
    without a restart."""
    def _f(key, default):
        try:
            return float(settings.get(key, default))
        except (TypeError, ValueError):
            return default
    return CaptureConfig(_f("stt.pause_threshold", 1.2),
                         _f("stt.phrase_time_limit", 30.0),
                         _f("stt.listen_timeout", 8.0),
                         _f("stt.recalibrate_every_s", 180.0))


def get_recognizer():
    """The shared Recognizer, REBUILT when its settings change.

    It used to be a plain singleton, so `pause_threshold` was written exactly
    once per process — meaning a settings change could never take effect and the
    new settings would have been a lie. Rebuilding also forces recalibration,
    since the old energy baseline belongs to the old configuration.
    """
    global _recognizer, _recognizer_config, _calibrated_index
    import speech_recognition as sr  # lazy

    cfg = capture_config()
    key = (cfg.pause_threshold,)
    if _recognizer is None or _recognizer_config != key:
        r = sr.Recognizer()
        r.dynamic_energy_threshold = True
        r.pause_threshold = cfg.pause_threshold
        _recognizer = r
        _recognizer_config = key
        _calibrated_index = None      # new config -> old baseline is meaningless
    return _recognizer


def should_calibrate(index: int | None, now: float) -> bool:
    """Re-measure ambient noise? A new device always does; otherwise once the
    interval has elapsed. `recalibrate_every_s = 0` disables re-calibration for
    a stable environment where the 0.6s pause is not worth paying."""
    if _calibrated_index != index:
        return True
    interval = capture_config().recalibrate_every_s
    if interval <= 0:
        return False
    return (now - _last_calibrated_at) >= interval


def looks_truncated(audio, limit: float) -> bool:
    """Did this capture run into `phrase_time_limit`?

    speech_recognition does NOT raise there — it breaks out and returns the
    partial audio — so a capture whose duration reaches the cap is the ONLY
    evidence that the user was cut off mid-sentence. Never raises: a detection
    helper must not be able to break capture itself.
    """
    try:
        rate = int(getattr(audio, "sample_rate", 0))
        width = int(getattr(audio, "sample_width", 0))
        data = getattr(audio, "frame_data", None)
        if not rate or not width or not data:
            return False
        return (len(data) / float(rate * width)) >= (limit - 0.25)
    except Exception:
        return False


def _on_truncated(audio) -> None:
    """Seam so the caller (and tests) can see a cut-off happen."""
    print("  [mic] your sentence hit the recording limit and was cut short — "
          "raise stt.phrase_time_limit in settings if this keeps happening")


def note_truncation(audio, limit: float) -> bool:
    """Report a truncated capture instead of silently handing STT half a
    sentence, which is what produced 'it doesn't understand what I want'."""
    if looks_truncated(audio, limit):
        try:
            _on_truncated(audio)
        except Exception:
            pass
        return True
    return False


_open_cache: dict[int, bool] = {}


def _can_open(index: int | None) -> bool:
    """Does this device actually yield a capture stream?

    Cached: probing costs a device open, and find_real_mic() runs on every
    listen. Cleared by forget_device_probe() whenever a capture fails, so a
    re-plugged headset is re-discovered rather than being written off forever.
    Never raises — an un-probeable device simply reads as unusable.
    """
    if index in _open_cache:
        return _open_cache[index]
    ok = False
    try:
        import speech_recognition as sr
        with sr.Microphone(device_index=index) as source:
            ok = source.stream is not None
    except Exception:
        ok = False
    _open_cache[index] = ok
    return ok


def forget_device_probe() -> None:
    """Drop cached open-probes so devices are re-evaluated (called when a
    capture fails, and whenever the mic pin changes)."""
    _open_cache.clear()


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
    if not ranked:
        return None, "system default"

    # A NAME IS A GUESS; OPENING IS GROUND TRUTH (slice 58). Found on the owner's
    # real machine: the top-ranked "Microphone (Realtek HD Audio Mic input)" is
    # an EMPTY onboard jack — nothing plugged in — so it never opens, while his
    # actual Samsung earphone mic sat lower in the ranking and was never tried.
    # The result is JARVIS silently having no microphone at all, and which
    # device wins depends on enumeration, so it can change between runs. That is
    # indistinguishable from "the listening is inconsistent".
    for _rank, idx, name in sorted(ranked):
        if _can_open(idx):
            return idx, name
    # Nothing opened (every mic busy, or a transient device error): fall back to
    # the ranked favourite rather than "system default", so behaviour degrades to
    # exactly what it was before this probe existed.
    _rank, idx, name = min(ranked)
    return idx, name


def listen_once(timeout: float | None = None, phrase_time_limit: float | None = None):
    """Capture one utterance. Returns sr.AudioData or None (timeout / device issue).

    Slice 58: timing now comes from settings (`stt.*`) rather than hardcoded
    literals, ambient noise is re-measured periodically instead of once per
    process, and a `phrase_time_limit` truncation is REPORTED rather than
    silently shipping half a sentence to STT.
    """
    global _calibrated_index, _last_calibrated_at
    require_audio_stdlib()
    import speech_recognition as sr  # lazy

    cfg = capture_config()
    if timeout is None:
        timeout = cfg.listen_timeout
    if phrase_time_limit is None:
        phrase_time_limit = cfg.phrase_time_limit

    with _lock:
        recognizer = get_recognizer()

        index, _name = find_real_mic()
        try:
            # NOTE: no sample_rate argument — device default only (lesson #1).
            with sr.Microphone(device_index=index) as source:
                if should_calibrate(index, time.monotonic()):
                    recognizer.adjust_for_ambient_noise(source, duration=0.6)
                    _calibrated_index = index
                    _last_calibrated_at = time.monotonic()
                audio = recognizer.listen(source, timeout=timeout,
                                          phrase_time_limit=phrase_time_limit)
                note_truncation(audio, phrase_time_limit)
                # Slice 57: `listen` returns ~pause_threshold (0.8s) AFTER the
                # user actually stopped talking. The harness subtracts that and
                # SAYS SO rather than quietly flattering the number.
                timing.mark("speech_end")
                return audio
        except sr.WaitTimeoutError:
            return None  # silence — caller just listens again
        except OSError as exc:
            print(f"  [mic] device problem ({exc}) — re-detecting on next attempt")
            _calibrated_index = None
            forget_device_probe()   # a re-plugged device must be re-discovered
            return None
        except Exception as exc:
            print(f"  [mic] capture failed ({exc}) — retrying")
            return None
