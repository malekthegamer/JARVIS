"""system_control primitives (slice 8): volume/mute via pycaw, media keys
via VK codes, brightness via screen_brightness_control.

All AUTO tier (spec §1.4): instantly reversible, non-committal, inputs
clamped to 0–100. Every entry returns {"ok", "message", ...} and never
raises. Brightness is hardware-dependent — on displays without a laptop
panel or DDC/CI (this machine, probe-verified) it fails HONESTLY rather
than pretending.
"""
from __future__ import annotations

import subprocess
import time
from contextlib import contextmanager

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
        # sbc silently no-ops when no display is addressable (found live in
        # the slice-8 E2E: "set" claimed success on a monitor that can't be
        # controlled). Success REQUIRES a readback — no evidence, no claim.
        values = sbc.get_brightness()
        if not values:
            raise RuntimeError("no displays reported back")
        return {"ok": True,
                "message": f"Brightness set to {clamped:g}%{note} "
                           f"(readback {values[0]}%)."}
    except Exception:
        return {"ok": False, "message": _UNSUPPORTED_BRIGHTNESS}


# ---------- DND / Focus Assist (slice 12) ----------
# Stage 0 proved the WNF quiet-hours write is a no-op on the user-facing toggle
# (NTSTATUS 0, changestamp advances, but the real "Do not disturb" switch never
# moves). The honest method drives that switch directly through UIA on the
# PUBLIC ms-settings:notifications surface and CONFIRMS by reading the toggle
# back — it manipulates and reads the exact control the user sees, so it cannot
# claim a success that didn't happen. AUTO tier (reversible), but visible: a
# Settings window opens briefly. Never raises.

# Identity of the DND ToggleSwitch (both observed on build 26200; either alone
# suffices, so a rename of one channel doesn't blind us):
_DND_AID_NEEDLES = ("quiethours", "mutenotification")  # in the automation_id
_DND_NAME = "do not disturb"                            # visible name fallback
_DND_OPEN_TIMEOUT_S = 20.0

_DND_UNAVAILABLE = ("Do Not Disturb control isn't available on this Windows "
                    "build — the notifications toggle may have moved or been "
                    "renamed.")
_DND_NO_RESPONSE = ("Couldn't change Do Not Disturb — the toggle didn't "
                    "respond.")


def _dnd_word(state) -> str:
    return "on" if state else "off"


def _settings_window():
    from pywinauto import Desktop  # lazy
    for w in Desktop(backend="uia").windows():
        if w.window_text() == "Settings":
            return w
    return None


def _find_dnd_control(window):
    """The DND ToggleSwitch in an open Settings window, or None. Matches by
    automation_id (semantic, primary) or the visible name (fallback)."""
    for d in window.descendants():
        try:
            d.get_toggle_state()          # must expose TogglePattern
        except Exception:
            continue
        try:
            aid = (d.element_info.automation_id or "").lower()
            nm = (d.window_text() or "").strip().lower()
        except Exception:
            continue
        if all(n in aid for n in _DND_AID_NEEDLES) or nm == _DND_NAME:
            return d
    return None


class _DndToggle:
    """Live wrapper over the Settings DND switch. Re-resolves the element on
    EVERY call — a UIA handle goes stale across an invoke, so caching it would
    read the pre-toggle value and manufacture a false 'confirmed'."""
    def __init__(self, window):
        self._window = window

    def _control(self):
        ctrl = _find_dnd_control(self._window)
        if ctrl is None:
            raise RuntimeError("DND toggle vanished mid-operation")
        return ctrl

    def state(self) -> int:
        return int(self._control().get_toggle_state())

    def toggle(self) -> None:
        self._control().toggle()


@contextmanager
def _dnd_session():
    """Yield a _DndToggle for the real Settings DND switch, or None if it can't
    be found within the timeout. Opens ms-settings:notifications, and closes
    Settings again ONLY if WE launched it (never a window the user had open)."""
    _co_init()
    launched_by_us = False
    try:
        launched_by_us = _settings_window() is None
        # Activate/navigate to the notifications page (works whether or not a
        # Settings window already exists).
        subprocess.Popen(["cmd", "/c", "start", "ms-settings:notifications"])

        window = None
        found = False
        deadline = time.time() + _DND_OPEN_TIMEOUT_S
        while time.time() < deadline:
            window = _settings_window()
            if window is not None:
                try:
                    found = _find_dnd_control(window) is not None
                except Exception:
                    found = False
                if found:
                    break
            time.sleep(0.4)

        yield _DndToggle(window) if (window is not None and found) else None
    finally:
        if launched_by_us:
            subprocess.run(["taskkill", "/IM", "SystemSettings.exe", "/F"],
                           capture_output=True)


def get_dnd() -> dict:
    """Read the real Do Not Disturb state (opens Settings to read the authoritative
    toggle; never changes it). {ok, enabled, message}."""
    try:
        with _dnd_session() as toggle:
            if toggle is None:
                return {"ok": False, "enabled": None, "message": _DND_UNAVAILABLE}
            try:
                state = toggle.state()
            except Exception:
                return {"ok": False, "enabled": None, "message": _DND_UNAVAILABLE}
            return {"ok": True, "enabled": bool(state),
                    "message": f"Do Not Disturb is {_dnd_word(state)}."}
    except Exception:
        return {"ok": False, "enabled": None, "message": _DND_UNAVAILABLE}


def set_dnd(enabled) -> dict:
    """Turn Do Not Disturb on/off by driving the real Settings toggle, then
    CONFIRM by readback (the sbc lesson: no evidence, no success claim). AUTO
    tier. {ok, enabled, message}. Never raises."""
    want = 1 if enabled else 0
    try:
        with _dnd_session() as toggle:
            if toggle is None:
                return {"ok": False, "enabled": None, "message": _DND_UNAVAILABLE}
            try:
                current = toggle.state()
            except Exception:
                return {"ok": False, "enabled": None, "message": _DND_UNAVAILABLE}
            if current == want:
                # previous == enabled here — the caller can see nothing
                # actually changed (slice 26: no undo entry for a no-op).
                return {"ok": True, "enabled": bool(want), "previous": bool(want),
                        "message": f"Do Not Disturb is already {_dnd_word(want)}."}
            toggle.toggle()
            time.sleep(0.5)               # let the switch settle before readback
            try:
                back = toggle.state()
            except Exception:
                return {"ok": False, "enabled": None, "message": _DND_NO_RESPONSE}
            if back != want:
                return {"ok": False, "enabled": bool(back), "message": _DND_NO_RESPONSE}
            # "previous" (slice 26) = the state read before toggling — the
            # undo capture point, surfaced from the read this function
            # already does (a separate get_dnd would open Settings twice).
            return {"ok": True, "enabled": bool(want), "previous": bool(current),
                    "message": f"Do Not Disturb turned {_dnd_word(want)} "
                               f"(readback confirmed)."}
    except Exception:
        return {"ok": False, "enabled": None, "message": _DND_UNAVAILABLE}


# ---------------------------------------------------------------- slice 31:
# clipboard (spec §1.2 system_control). pyperclip (a declared dep) over the
# Windows clipboard. No window/focus targeting — it's process-global OS state.
# _clip_get/_clip_set are thin seams so tests can mock them (the _dnd_session
# fake pattern). Both verbs return {ok, ..., message} and never raise.

CLIPBOARD_MAX_CHARS = 100_000     # cap a read so a huge copy can't blow up context


def _clip_get() -> str:
    import pyperclip  # lazy
    return pyperclip.paste() or ""


def _clip_set(text: str) -> None:
    import pyperclip  # lazy
    pyperclip.copy(text)


def get_clipboard() -> dict:
    """Read the clipboard TEXT (AUTO). Non-text (image/files) reads as '' on
    Windows -> reported honestly. Oversize text truncated. Never raises."""
    try:
        text = _clip_get() or ""
        if not text:
            return {"ok": True, "text": "",
                    "message": "The clipboard is empty (or holds non-text I can't read)."}
        if len(text) > CLIPBOARD_MAX_CHARS:
            return {"ok": True, "text": text[:CLIPBOARD_MAX_CHARS],
                    "message": f"Read the clipboard (truncated to "
                               f"{CLIPBOARD_MAX_CHARS} of {len(text)} chars)."}
        return {"ok": True, "text": text,
                "message": f"Read the clipboard ({len(text)} chars)."}
    except Exception as exc:
        return {"ok": False, "text": "",
                "message": f"Couldn't read the clipboard: {exc}"}


def set_clipboard(text) -> dict:
    """Put TEXT on the clipboard (AUTO). Reads the PREVIOUS text first and
    surfaces it as 'previous' so an overwrite can be undone (slice 26). Never
    raises."""
    text = "" if text is None else str(text)
    try:
        try:
            previous = _clip_get() or ""
        except Exception:
            previous = ""          # best-effort; undo simply won't be offered
        _clip_set(text)
        return {"ok": True, "previous": previous,
                "message": f"Put {len(text)} chars on the clipboard "
                           f"(paste with Ctrl+V)."}
    except Exception as exc:
        return {"ok": False, "previous": "",
                "message": f"Couldn't set the clipboard: {exc}"}
