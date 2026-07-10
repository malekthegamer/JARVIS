"""Browser tab primitives (slice 8): list_tabs (AUTO) + close_tabs (CONFIRM).

Approach: UI Automation on the running browser's tab strip (probe-verified:
Chrome exposes every tab as a TabItem with its real title and a close-button
child). No CDP relaunch, no browser-specific API; works for Chromium-family
browsers (Chrome, Edge, Brave).

Safety doctrine (spec §1.4 + slice-3/4 precedents):
- close_tabs is CONFIRM, always: closing a tab destroys page state and is
  not reliably reversible. It gates ONCE per batch — the modal description
  is computed from the RESOLVED tab list (count, kept titles, samples), and
  it NAMES escalations: closing every tab closes the window itself.
- No pointless modals: no matching browser window or no matching tabs ->
  describe returns None and the executor fails cleanly.
- Execution re-resolves; tabs that vanished since gate time are skipped and
  reported, never guessed at.
"""
from __future__ import annotations

import time

from jarvis.primitives.ui_tree import _co_init

# Only real browsers — Electron apps (Discord, VS Code) share the window
# class, so identify by owning process, not by class name.
BROWSER_EXES = {"chrome.exe", "msedge.exe", "brave.exe"}
MAX_LIST = 60  # tab titles returned to the model (payload discipline)


def _browser_windows() -> list:
    """UIA wrappers for visible browser windows (by owning process exe)."""
    _co_init()
    out = []
    try:
        import psutil
        from pywinauto import Desktop
        for w in Desktop(backend="uia").windows():
            try:
                pid = w.element_info.process_id
                if pid and psutil.Process(pid).name().lower() in BROWSER_EXES:
                    out.append(w)
            except Exception:
                continue
    except Exception:
        pass
    return out


def _tabs_of(win) -> list:
    """TabItem wrappers of one browser window (document tabs only)."""
    try:
        return [t for t in win.descendants(control_type="TabItem")
                if (t.window_text() or "").strip()]
    except Exception:
        return []


def _resolve_window(window_hint: str | None):
    """The browser window whose title OR any tab title matches the hint.
    No hint: exactly one browser window -> that one; several -> None
    (ambiguity refuses, never guesses — slice-4 doctrine)."""
    wins = _browser_windows()
    if not wins:
        return None
    hint = str(window_hint or "").strip().casefold()
    if not hint:
        return wins[0] if len(wins) == 1 else None
    for w in wins:
        try:
            if hint in (w.window_text() or "").casefold():
                return w
        except Exception:
            continue
    for w in wins:
        if any(hint in (t.window_text() or "").casefold() for t in _tabs_of(w)):
            return w
    return None


def list_tabs(window_hint: str | None = None) -> dict:
    """AUTO. Titles of the tabs in the matched browser window."""
    win = _resolve_window(window_hint)
    if win is None:
        return {"ok": False, "tabs": [], "window": None,
                "message": "No browser window matching "
                           f"'{window_hint}' is open." if window_hint else
                           "No (single) browser window found — give me a "
                           "window title to disambiguate."}
    titles = [t.window_text() for t in _tabs_of(win)][:MAX_LIST]
    wtitle = win.window_text()
    return {"ok": True, "tabs": titles, "window": wtitle,
            "message": f"{len(titles)} tab(s) in '{wtitle}': "
                       + "; ".join(f"[{i+1}] {t}" for i, t in enumerate(titles))}


def _plan(window_hint, keep_matching, close_matching) -> dict | None:
    """Resolve the batch NOW: which tabs close, which stay. None = nothing
    actionable (no window / no filter / no targets)."""
    keep = str(keep_matching or "").strip().casefold()
    close = str(close_matching or "").strip().casefold()
    if bool(keep) == bool(close):  # neither, or both
        return None
    win = _resolve_window(window_hint)
    if win is None:
        return None
    tabs_ = _tabs_of(win)
    if not tabs_:
        return None
    titles = [t.window_text() for t in tabs_]
    if keep:
        targets = [t for t in titles if keep not in t.casefold()]
        kept = [t for t in titles if keep in t.casefold()]
    else:
        targets = [t for t in titles if close in t.casefold()]
        kept = [t for t in titles if close not in t.casefold()]
    if not targets:
        return None
    return {"window": win, "window_title": win.window_text(),
            "titles": titles, "targets": targets, "kept": kept}


def describe_close_tabs(args: dict) -> str | None:
    """CONFIRM text from the RESOLVED plan — count, kept titles, samples,
    and the window-close escalation when every tab is going."""
    plan = _plan(args.get("window"), args.get("keep_matching"),
                 args.get("close_matching"))
    if plan is None:
        return None
    n = len(plan["targets"])
    sample = "; ".join(t[:40] for t in plan["targets"][:3])
    if len(plan["targets"]) > 3:
        sample += "; …"
    if plan["kept"]:
        kept = "; ".join(t[:40] for t in plan["kept"][:3])
        tail = f", keeping {len(plan['kept'])} ({kept})"
    else:
        tail = (", keeping NONE — every tab goes and the WINDOW itself "
                "will close")
    if plan["kept"] == [] or n == len(plan["titles"]):
        pass  # tail already warns
    return (f"Close {n} tab(s) in '{plan['window_title']}'"
            f" ({sample}){tail}")


def close_tabs(window_hint=None, keep_matching=None, close_matching=None) -> dict:
    """CONFIRM. Execute the batch: re-resolve, close each target via its own
    close button (Invoke; select+ctrl+w fallback), verify by re-count."""
    plan = _plan(window_hint, keep_matching, close_matching)
    if plan is None:
        win = _resolve_window(window_hint)
        if win is None:
            return {"ok": False, "closed": 0,
                    "message": f"No browser window matching '{window_hint}'."}
        return {"ok": False, "closed": 0,
                "message": "Nothing to close — no tabs match that filter "
                           "(or the filter keeps everything)."}
    wanted = list(plan["targets"])
    before = len(plan["titles"])
    closed = 0
    gone = 0
    for title in wanted:
        # re-resolve THIS tab right before acting (it may have vanished)
        current = _tabs_of(plan["window"])
        match = next((t for t in current if t.window_text() == title), None)
        if match is None:
            gone += 1
            continue
        if _close_tab(plan["window"], match):
            closed += 1
        time.sleep(0.15)  # let the strip reflow before the next resolve
    remain = len(_tabs_of(plan["window"]))
    verify = f"VERIFY: {before} tabs before, {remain} remain."
    note = f" ({gone} already gone)" if gone else ""
    ok = closed > 0 or gone == len(wanted)
    return {"ok": ok, "closed": closed,
            "message": f"Closed {closed} of {len(wanted)} tab(s){note}. {verify}"}


def _close_tab(win, tab) -> bool:
    """Close one TabItem: its own close button first, ctrl+w fallback."""
    try:
        buttons = tab.children(control_type="Button")
        if buttons:
            try:
                buttons[0].invoke()
                return True
            except Exception:
                pass
        tab.select()
        time.sleep(0.1)
        win.type_keys("^w", set_foreground=True)
        return True
    except Exception:
        return False
