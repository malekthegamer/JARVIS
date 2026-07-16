"""close_window — CONFIRM tier. Closes one top-level window by title.

Matching is exact-first, then substring (case-insensitive), and the CONFIRM
modal names the ACTUAL matched title via describe_close() — the user
approves the real target, not the model's paraphrase. describe_close()
returning None means no such window: the executor skips both the modal and
the action (no pointless prompts for nonexistent windows).
"""
from __future__ import annotations

import time

from jarvis.primitives.ui_tree import _co_init, _win32_windows


def _enum_windows() -> list[tuple[str, int, int]]:
    """(title, owning pid, hwnd) for every titled top-level window.

    Slice 21: sources from the fast win32 enumeration (was a ~1.7 s pywinauto
    UIA walk). The hwnd lets the resolver hand pywinauto a handle instead of
    re-enumerating to match a title string (which could mismatch across the
    win32/UIA name APIs). [] on failure."""
    return _win32_windows()


def find_window(title_substring: str) -> tuple[int | None, str | None]:
    """(hwnd, title) of the best-matching open window, or (None, None).
    Passes in order: exact title, substring title, then OWNING PROCESS name —
    apps like Spotify retitle their window to the playing track (slice-6
    acceptance finding), so '<hint>.exe' as the window's process must still
    match or every targeted action breaks mid-task."""
    needle = str(title_substring or "").strip().casefold()
    if not needle:
        return None, None
    windows = _enum_windows()
    for title, _pid, hwnd in windows:
        if title.casefold() == needle:
            return hwnd, title
    for title, _pid, hwnd in windows:
        if needle in title.casefold():
            return hwnd, title
    want = needle if needle.endswith(".exe") else needle + ".exe"
    try:
        import psutil
        for title, pid, hwnd in windows:
            try:
                if pid and psutil.Process(pid).name().casefold() == want:
                    return hwnd, title
            except Exception:
                continue
    except Exception:
        pass
    return None, None


def find_window_title(title_substring: str) -> str | None:
    """The actual title of the best-matching open window, or None.
    Thin wrapper over find_window (contract preserved for existing callers)."""
    return find_window(title_substring)[1]


def close_window(title_substring: str) -> dict:
    """CONFIRM tier. Returns {"ok", "message"} — never raises.

    Slice 21: resolve the hwnd and wrap THAT handle (no title re-enumeration —
    which both cost ~1.7 s and risked a win32-vs-UIA name mismatch)."""
    needle = str(title_substring or "").strip()
    try:
        hwnd, matched = find_window(needle)
        if hwnd is None:
            return {"ok": False,
                    "message": f"No open window matching '{needle}'."}
        _co_init()
        from pywinauto import Desktop  # lazy
        Desktop(backend="uia").window(handle=hwnd).wrapper_object().close()
        # verify it actually went away (by handle — unambiguous)
        deadline = time.time() + 3
        while time.time() < deadline:
            if find_window(matched)[0] is None:
                return {"ok": True, "message": f"Closed window '{matched}'."}
            time.sleep(0.3)
        return {"ok": False,
                "message": f"Sent close to '{matched}' but it's still open "
                           f"(it may be asking to save changes)."}
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't close '{needle}': {exc}"}


def describe_close(args: dict) -> str | None:
    matched = find_window_title(str(args.get("title", "")))
    return None if matched is None else f"Close window '{matched}'"
