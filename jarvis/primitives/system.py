"""system_control primitives (slice 8): volume/mute via pycaw, media keys
via VK codes, brightness via screen_brightness_control.

All AUTO tier (spec §1.4): instantly reversible, non-committal, inputs
clamped to 0–100. Every entry returns {"ok", "message", ...} and never
raises. Brightness is hardware-dependent — on displays without a laptop
panel or DDC/CI (this machine, probe-verified) it fails HONESTLY rather
than pretending.
"""
from __future__ import annotations

from jarvis.primitives.ui_tree import _co_init

# Windows virtual-key codes for the hardware media keys.
MEDIA_KEYS = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "prev": 0xB1,
    "stop": 0xB2,
}

_UNSUPPORTED_BRIGHTNESS = ("This display doesn't support software brightness "
                           "control (no laptop panel and DDC/CI isn't "
                           "responding).")


def _endpoint():
    """The default speaker endpoint's volume interface. COM per-thread init
    (server tool calls run in threadpool workers — ui_tree precedent)."""
    _co_init()
    from ctypes import POINTER, cast

    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    device = AudioUtilities.GetSpeakers()
    if hasattr(device, "EndpointVolume"):  # modern pycaw AudioDevice wrapper
        return device.EndpointVolume
    iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def _clamp(value, name: str) -> tuple[float | None, str]:
    """(clamped 0-100, clamp-note) or (None, error message)."""
    try:
        want = float(value)
    except (TypeError, ValueError):
        return None, f"'{value}' is not a number."
    clamped = max(0.0, min(100.0, want))
    note = "" if clamped == want else f" (clamped from {want:g} — {name} is 0–100)"
    return clamped, note


def get_volume() -> dict:
    try:
        vol = _endpoint()
        level = round(vol.GetMasterVolumeLevelScalar() * 100)
        muted = bool(vol.GetMute())
        return {"ok": True, "level": level, "muted": muted,
                "message": f"Volume is {level}%" + (" (muted)" if muted else "") + "."}
    except Exception as exc:
        return {"ok": False, "level": None, "muted": None,
                "message": f"Couldn't read the volume: {exc}"}


def set_volume(level) -> dict:
    clamped, note = _clamp(level, "volume")
    if clamped is None:
        return {"ok": False, "message": note}
    try:
        _endpoint().SetMasterVolumeLevelScalar(clamped / 100.0, None)
        return {"ok": True, "message": f"Volume set to {clamped:g}%{note}."}
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't set the volume: {exc}"}


def set_mute(muted) -> dict:
    try:
        _endpoint().SetMute(bool(muted), None)
        return {"ok": True,
                "message": "Muted." if muted else "Unmuted."}
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't change mute: {exc}"}


def media_key(key: str) -> dict:
    """Press one hardware media key. Unknown keys fail closed, naming the
    allowed set — never guess a keycode."""
    key = str(key or "").strip().lower()
    vk = MEDIA_KEYS.get(key)
    if vk is None:
        allowed = ", ".join(sorted(MEDIA_KEYS))
        return {"ok": False,
                "message": f"Unknown media key '{key}'. Allowed: {allowed}."}
    try:
        import ctypes
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP
        return {"ok": True, "message": f"Sent media key '{key}'."}
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't send media key '{key}': {exc}"}


def get_brightness() -> dict:
    try:
        import screen_brightness_control as sbc
        values = sbc.get_brightness()
        if not values:
            raise RuntimeError("no displays reported")
        return {"ok": True, "level": int(values[0]),
                "message": f"Brightness is {values[0]}%."}
    except Exception:
        return {"ok": False, "level": None, "message": _UNSUPPORTED_BRIGHTNESS}


def set_brightness(level) -> dict:
    clamped, note = _clamp(level, "brightness")
    if clamped is None:
        return {"ok": False, "message": note}
    try:
        import screen_brightness_control as sbc
        sbc.set_brightness(int(clamped))
        return {"ok": True, "message": f"Brightness set to {clamped:g}%{note}."}
    except Exception:
        return {"ok": False, "message": _UNSUPPORTED_BRIGHTNESS}
