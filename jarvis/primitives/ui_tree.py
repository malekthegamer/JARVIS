"""read_ui_tree — the slice-2 perception primitive (Windows UI Automation
via pywinauto, "uia" backend so UWP apps like Win11 Notepad are visible).

Server tool calls run in threadpool worker threads where COM is not
initialized — every entry point calls _co_init() first (the failure is
intermittent, so this is belt-and-braces by design). Traversal is depth-,
count- and wall-clock-limited: a partial tree beats a hung agent.
"""
from __future__ import annotations

import time


def _co_init() -> None:
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass  # already initialized on this thread, or pythoncom unavailable


def list_windows() -> list[str]:
    """Titles of visible top-level windows. Never raises — [] on failure."""
    _co_init()
    try:
        from pywinauto import Desktop  # lazy
        return [w.window_text() for w in Desktop(backend="uia").windows()
                if w.window_text().strip()]
    except Exception:
        return []


def window_present(title_substring: str) -> bool:
    """Case-insensitive substring match against open window titles."""
    needle = title_substring.lower()
    return any(needle in title.lower() for title in list_windows())


def window_present_for_process(exe_name: str) -> bool:
    """Is any visible window OWNED by a process with this exe name?
    Needed because some apps retitle their window away from their own name
    (Spotify shows the playing track — slice-6 acceptance finding), which
    false-negatives the title check. Never raises — False on failure."""
    _co_init()
    want = exe_name.lower()
    try:
        import psutil
        from pywinauto import Desktop  # lazy
        for w in Desktop(backend="uia").windows():
            try:
                pid = w.element_info.process_id
                if pid and psutil.Process(pid).name().lower() == want:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def read_ui_tree(max_windows: int = 8, max_depth: int = 2,
                 budget_s: float = 4.0) -> str:
    """Compact text listing of on-screen windows and their top controls,
    for the model and for verification. Partial output on budget exhaustion."""
    _co_init()
    deadline = time.time() + budget_s
    lines: list[str] = []
    try:
        from pywinauto import Desktop  # lazy
        windows = [w for w in Desktop(backend="uia").windows()
                   if w.window_text().strip()][:max_windows]
        for w in windows:
            lines.append(f"window: {w.window_text()!r}")
            if time.time() > deadline:
                lines.append("  … (time budget reached)")
                break
            lines.extend(_walk(w, depth=1, max_depth=max_depth, deadline=deadline))
    except Exception as exc:
        return f"ui tree unavailable ({type(exc).__name__}: {exc})"
    return "\n".join(lines) if lines else "no visible windows"


def _walk(element, depth: int, max_depth: int, deadline: float) -> list[str]:
    if depth > max_depth or time.time() > deadline:
        return []
    out: list[str] = []
    try:
        children = element.children()
    except Exception:
        return []
    for child in children[:12]:
        try:
            ctype = child.element_info.control_type or "Control"
            name = (child.window_text() or "").strip()
        except Exception:
            continue
        if name:
            out.append(f"{'  ' * depth}- {ctype}: {name!r}")
        out.extend(_walk(child, depth + 1, max_depth, deadline))
        if time.time() > deadline:
            break
    return out
