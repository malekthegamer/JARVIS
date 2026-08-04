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
    """May we re-measure ambient noise RIGHT NOW?

    SLICE 59 INVERTED THIS. Slice 58 ran calibration on a timer, on the capture
    path — which meant it fired immediately after the "I'm listening" earcon,
    i.e. exactly while the user was speaking. speech_recognition's own docstring
    says adjust_for_ambient_noise "should be used on periods of audio WITHOUT
    speech", and calibrating on speech drives energy_threshold to ~1.5x the
    user's voice, after which their real speech never registers as speech-start
    and listen() waits out the whole timeout. That produced the reported "it
    sits listening too long", every ~3 minutes, with no visible pattern.

    Now calibration is only ever performed at ONE moment: right after a capture
    has timed out, while the mic is still open. Nothing being heard is both
    proof of silence and evidence the threshold may be too high, so it is the
    only place where measuring is both safe and warranted.

    `recalibrate_every_s` is now a MINIMUM GAP between measurements (so a noisy
    room cannot thrash), not a schedule that triggers them. 0 means "no rate
    limit", not "never".
    """
    if _calibrated_index != index:
        return True                      # a device we have never measured
    interval = capture_config().recalibrate_every_s
    if interval <= 0:
        return True
    return (now - _last_calibrated_at) >= interval


# A threshold BELOW room noise is catastrophic, and in the opposite direction to
# the bug slice 59 set out to fix. listen() would treat ambient as speech-start
# immediately, then never see energy drop "below" it to end the phrase, so it
# records all the way to phrase_time_limit (30s) and hands STT a wall of noise —
# which presents as "it sits listening too long" just like the original report.
# Measured on this machine: adjust_for_ambient_noise converged to 20 against a
# real ambient RMS of ~43, i.e. it undershot on a briefly-quiet 0.6s sample.
_MIN_ENERGY_THRESHOLD = 50.0
_AMBIENT_HEADROOM = 2.0


def calibrate_with_floor(recognizer, source) -> float:
    """Re-measure ambient noise, then guarantee the threshold sits ABOVE it.

    `adjust_for_ambient_noise` alone is not enough: it converges from wherever
    the threshold happens to be, over a short window, so a momentarily quiet
    sample can leave it under the real noise floor. We therefore measure the
    room directly afterwards and clamp. Never raises.
    """
    try:
        recognizer.adjust_for_ambient_noise(source, duration=0.6)
    except Exception:
        pass
    rms = 0
    try:
        import audioop
        chunks = max(1, int(0.3 * source.SAMPLE_RATE / source.CHUNK))
        frames = [source.stream.read(source.CHUNK) for _ in range(chunks)]
        rms = audioop.rms(b"".join(frames), source.SAMPLE_WIDTH)
    except Exception:
        pass
    floor = max(_MIN_ENERGY_THRESHOLD, rms * _AMBIENT_HEADROOM)
    try:
        if recognizer.energy_threshold < floor:
            recognizer.energy_threshold = floor
        return float(recognizer.energy_threshold)
    except Exception:
        return 0.0


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


# Probe results keyed by device NAME -> (usable, when_measured).
#
# SLICE 59 fixed three things here. It was keyed by device INDEX, on a machine
# where indices provably shift (44 -> 45 -> 39 -> 1 observed within minutes), so
# a cached answer could describe a completely different device. It was mutated
# from two threads with no lock — listen_once holds `_lock` while the wake
# listener calls find_real_mic() outside it. And a `False` was cached FOREVER,
# so a microphone that merely happened to be busy at probe time (the wake
# listener holds the real mic continuously) was written off for the whole
# process, after which the fallback returned the very device the probe exists to
# reject. A dedicated lock is used because `_lock` is already held by
# listen_once when it calls in here.
_probe_lock = threading.Lock()
_open_cache: dict[str, tuple[bool, float]] = {}
_PROBE_FAIL_TTL_S = 60.0


def _can_open(index: int | None, name: str = "") -> bool:
    """Does this device actually yield a capture stream?

    Successes are cached indefinitely; FAILURES expire, because "unusable" is
    very often just "busy right now" and must be allowed to heal. Never raises —
    an un-probeable device simply reads as unusable.
    """
    key = (name or f"#{index}").strip().lower()
    now = time.monotonic()
    with _probe_lock:
        hit = _open_cache.get(key)
        if hit is not None:
            usable, when = hit
            if usable or (now - when) < _PROBE_FAIL_TTL_S:
                return usable
    ok = False
    try:
        import speech_recognition as sr
        with sr.Microphone(device_index=index) as source:
            ok = source.stream is not None
    except Exception:
        ok = False
    with _probe_lock:
        _open_cache[key] = (ok, now)
    return ok


def forget_device_probe() -> None:
    """Drop cached probes so every device is re-evaluated. Called when a capture
    hits a device error — a re-plugged headset must be re-discovered."""
    with _probe_lock:
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
        if _can_open(idx, name):
            return idx, name
    # Nothing opened (every mic busy, or a transient device error): fall back to
    # the ranked favourite rather than "system default", so behaviour degrades to
    # exactly what it was before this probe existed. SAY SO — silently returning
    # the exact device the probe exists to reject is how "listening randomly
    # stops working" looked from the outside.
    _rank, idx, name = min(ranked)
    print(f"  [mic] no microphone passed the open-probe; falling back to "
          f"{name!r} unverified — if listening fails, this is why")
    return idx, name


def listen_once(timeout: float | None = None, phrase_time_limit: float | None = None,
                on_ready=None):
    """Capture one utterance. Returns sr.AudioData or None (timeout / device issue).

    Slice 58: timing comes from settings (`stt.*`) rather than hardcoded
    literals, and a `phrase_time_limit` truncation is REPORTED rather than
    silently shipping half a sentence to STT.

    Slice 59 fixed two things slice 58 got wrong:
      * ambient calibration no longer runs before a capture (it was measuring
        the user's own voice as "background noise" — see should_calibrate);
      * `on_ready` fires once the microphone is genuinely open, so the
        "I'm listening" cue stops promising a live mic that isn't ready yet.
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
                # SLICE 59: the cue fires HERE — after the device is genuinely
                # open and immediately before listening. Slice 57 played it
                # before listen_once() was even called, so it promised a live
                # mic while device enumeration and opening were still running,
                # and every word said during that gap was lost.
                if on_ready is not None:
                    try:
                        on_ready()
                    except Exception:
                        pass      # a decorative cue must never cost an utterance
                try:
                    audio = recognizer.listen(
                        source, timeout=timeout,
                        phrase_time_limit=phrase_time_limit)
                except sr.WaitTimeoutError:
                    # NOTHING WAS HEARD. That is simultaneously proof of silence
                    # and evidence the threshold may be too high — the only
                    # moment where re-measuring ambient noise is both safe and
                    # warranted, and the mic is already open. See
                    # should_calibrate() for why this must never happen BEFORE a
                    # capture instead.
                    now = time.monotonic()
                    if should_calibrate(index, now):
                        level = calibrate_with_floor(recognizer, source)
                        _calibrated_index = index
                        _last_calibrated_at = now
                        print(f"  [mic] silent capture — re-measured ambient, "
                              f"speech threshold now {level:.0f}")
                    return None   # silence — caller just listens again
                if timing.enabled():
                    # SLICE 59: the owner reported failures with "no pattern I
                    # can see". Guessing was tried and was wrong, so the capture
                    # now states its own conditions. Opt-in (JARVIS_VOICE_TIMING=1)
                    # so normal runs stay quiet.
                    try:
                        import audioop
                        secs = (len(audio.frame_data)
                                / float(audio.sample_rate * audio.sample_width))
                        print(f"  [mic] heard {secs:.1f}s on {_name!r} "
                              f"(threshold {recognizer.energy_threshold:.0f}, "
                              f"rms {audioop.rms(audio.frame_data, audio.sample_width)})")
                    except Exception:
                        pass
                note_truncation(audio, phrase_time_limit)
                # Slice 57: `listen` returns ~pause_threshold AFTER the user
                # actually stopped talking. The harness subtracts that and SAYS
                # SO rather than quietly flattering the number.
                timing.mark("speech_end")
                return audio
        except OSError as exc:
            print(f"  [mic] device problem ({exc}) — re-detecting on next attempt")
            _calibrated_index = None
            forget_device_probe()   # a re-plugged device must be re-discovered
            return None
        except Exception as exc:
            print(f"  [mic] capture failed ({exc}) — retrying")
            return None
