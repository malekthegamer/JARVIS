"""close_window — CONFIRM tier. Closes one top-level window by title.

Matching is exact-first, then substring (case-insensitive), and the CONFIRM
modal names the ACTUAL matched title via describe_close() — the user
approves the real target, not the model's paraphrase. describe_close()
returning None means no such window: the executor skips both the modal and
the action (no pointless prompts for nonexistent windows).
"""
from __future__ import annotations

import time

from jarvis.primitives.ui_tree import _co_init


def _enum_windows() -> list[tuple[str, int]]:
    """(title, owning pid) for every titled top-level window. [] on failure."""
    _co_init()
    out: list[tuple[str, int]] = []
    try:
        from pywinauto import Desktop  # lazy
        for w in Desktop(backend="uia").windows():
            try:
                title = w.window_text()
                if title.strip():
                    out.append((title, w.element_info.process_id or 0))
            except Exception:
                continue
    except Exception:
        pass
    return out


def find_window_title(title_substring: str) -> str | None:
    """The actual title of the best-matching open window, or None.
    Passes: exact title, substring title, then OWNING PROCESS name —
    apps like Spotify retitle their window to the playing track (slice-6
    acceptance finding), so '<hint>.exe' as the window's process must
    still match or every targeted action breaks mid-task."""
    needle = str(title_substring or "").strip().casefold()
    if not needle:
        return None
    windows = _enum_windows()
    for title, _pid in windows:
        if title.casefold() == needle:
            return title
    for title, _pid in windows:
        if needle in title.casefold():
            return title
    want = needle if needle.endswith(".exe") else needle + ".exe"
    try:
        import psutil
        for title, pid in windows:
            try:
                if pid and psutil.Process(pid).name().casefold() == want:
                    return title
            except Exception:
                continue
    except Exception:
        pass
    return None


def close_window(title_substring: str) -> dict:
    """CONFIRM tier. Returns {"ok", "message"} — never raises."""
    needle = str(title_substring or "").strip()
    try:
        _co_init()
        from pywinauto import Desktop  # lazy
        matched = find_window_title(needle)
        if matched is None:
            return {"ok": False,
                    "message": f"No open window matching '{needle}'."}
        for w in Desktop(backend="uia").windows():
            if w.window_text() == matched:
                w.close()
                break
        # verify it actually went away
        deadline = time.time() + 3
        while time.time() < deadline:
            if find_window_title(matched) is None:
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
