"""Input synthesis (spec §1.2/§1.5) — click / type_text / press_keys.

Accessibility-first: targets are natural-language descriptions resolved
against the UIA tree to a real element (name + rect), never raw x/y from the
model. The cardinal rule: an unresolvable or ambiguous target FAILS and
names alternatives — it never guess-clicks.

Synthesis chain: pydirectinput (works in games/fullscreen, spec §1.5) →
pyautogui → pywinauto send_keys (unicode). Focus safety and the AUTO/CONFIRM
tier classifier live here too; the executor consumes classify_*() .
"""
from __future__ import annotations

import difflib
import re
import time

from jarvis.primitives.ui_tree import _co_init

# ---- element resolution tuning ----
_MAX_DEPTH = 4
_BUDGET_S = 4.0
_AMBIGUITY_MARGIN = 0.10       # top-2 within this → ambiguous, refuse
_FUZZY_FLOOR = 0.75            # difflib ratio below this doesn't count
_EDIT_TYPES = ("Edit", "Document", "Text")
_CONTROL_WORDS = {
    "button": "Button", "field": "Edit", "textbox": "Edit", "text box": "Edit",
    "text area": "Document", "textarea": "Document", "editor": "Document",
    "tab": "TabItem", "menu item": "MenuItem", "menu": "MenuItem",
    "checkbox": "CheckBox", "check box": "CheckBox", "link": "Hyperlink",
    "list item": "ListItem", "combo box": "ComboBox", "dropdown": "ComboBox",
}
_STOPWORDS = {"the", "a", "an", "my", "this", "that", "please", "click", "on"}

_CHUNK = 40                    # type in chunks, re-check focus between them
_FOCUS_SETTLE_S = 0.15
# Brief settle after focus, then RE-VERIFY the foreground right before sending
# (a fullscreen app can re-grab focus during the settle — the re-verify, not
# the delay, is the safety). Kept short so the theft window stays small.
_PRE_INPUT_SETTLE_S = 0.15

# ---- tier classification ----
# Decided from the RESOLVED element name / literal combo — not model paraphrase.
_DESTRUCTIVE_RE = re.compile(
    r"\b(send|submit|confirm|delete|remove|save|discard|buy|purchase|pay|order|"
    r"post|publish|reply|share|sign|apply|install|uninstall|format|empty)\b")
_DIALOG_COMMIT_RE = re.compile(r"\b(yes|ok|continue)\b")  # only inside a dialog
_MODS = {"ctrl", "control", "alt", "shift", "win"}
_AUTO_KEYS = {"up", "down", "left", "right", "tab", "esc", "escape",
              "home", "end", "pageup", "pagedown", "backspace"}
_AUTO_COMBOS = {"ctrl+c", "ctrl+v", "ctrl+x", "ctrl+a", "ctrl+z", "ctrl+y", "ctrl+f"}
_CONFIRM_COMBOS = {"enter", "ctrl+enter", "ctrl+shift+enter", "ctrl+s",
                   "alt+f4", "ctrl+w", "delete"}
_TERMINAL_HINTS = ("cmd", "command prompt", "powershell", "terminal",
                   "wt", "conhost", "console")
_COMBO_LABELS = {"ctrl+s": "save", "enter": "submit", "ctrl+enter": "submit",
                 "alt+f4": "close the window", "ctrl+w": "close the tab",
                 "delete": "delete"}


# ============================ resolution ============================

def _foreground_window():
    """The active top-level pywinauto window, or None."""
    _co_init()
    try:
        import win32gui
        from pywinauto import Desktop
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            return Desktop(backend="uia").window(handle=hwnd).wrapper_object()
    except Exception:
        pass
    return None


def _target_window(window_hint: str | None):
    if window_hint:
        from jarvis.primitives import windows as jwindows
        title = jwindows.find_window_title(window_hint)
        if title is None:
            return None, None
        try:
            _co_init()
            from pywinauto import Desktop
            for w in Desktop(backend="uia").windows():
                if w.window_text() == title:
                    return w, title
        except Exception:
            return None, None
        return None, None
    w = _foreground_window()
    return (w, w.window_text()) if w else (None, None)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().casefold())


def _is_dialog(win) -> bool:
    try:
        ct = win.element_info.control_type
        cn = win.element_info.class_name or ""
        return ct == "Dialog" or cn == "#32770"
    except Exception:
        return False


def _score(needle: str, name: str) -> float:
    """0..1 match of a resolved element name against the (control-word-
    stripped) description. Exact > startswith > word-boundary > fuzzy."""
    n, nm = _norm(needle), _norm(name)
    if not n or not nm:
        return 0.0
    if n == nm:
        return 1.0
    if nm.startswith(n) or n.startswith(nm):
        return 0.9
    if re.search(rf"\b{re.escape(n)}\b", nm):
        return 0.8
    ratio = difflib.SequenceMatcher(None, n, nm).ratio()
    return ratio if ratio >= _FUZZY_FLOOR else 0.0


def _wanted_control(description: str) -> str | None:
    d = _norm(description)
    for word, ctype in _CONTROL_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", d):
            return ctype
    return None


def _strip_control_words(description: str) -> str:
    d = _norm(description)
    for word in _CONTROL_WORDS:
        d = re.sub(rf"\b{re.escape(word)}\b", " ", d)
    tokens = [t for t in d.split() if t not in _STOPWORDS]
    return " ".join(tokens).strip()


def _iter_elements(window, deadline):
    """Visible, enabled, non-empty-rect descendants (depth/time-limited)."""
    out = []

    def walk(el, depth):
        if depth > _MAX_DEPTH or time.time() > deadline:
            return
        try:
            children = el.children()
        except Exception:
            return
        for child in children:
            if time.time() > deadline:
                return
            try:
                r = child.rectangle()
                if (child.is_visible() and child.is_enabled()
                        and r.width() > 0 and r.height() > 0):
                    out.append(child)
            except Exception:
                pass
            walk(child, depth + 1)

    walk(window, 0)
    return out


def resolve_target(description: str, window_hint: str | None = None) -> dict:
    """Resolve a description to one on-screen element. Returns
    {ok, element_name, control_type, window_title, rect, mid_point} on
    success, or {ok: False, message, candidates} — never coordinates on
    failure. Focuses the target window first so rects are valid."""
    description = str(description or "").strip()
    if not description:
        return {"ok": False, "message": "No target described.", "candidates": []}

    window, title = _target_window(window_hint)
    if window is None:
        return {"ok": False,
                "message": f"Couldn't find the window{f' matching {window_hint!r}' if window_hint else ''}.",
                "candidates": []}
    _acquire_focus(window, title)  # rects are only valid once the window is up

    wanted_type = _wanted_control(description)
    needle = _strip_control_words(description) or _norm(description)

    scored = []
    for el in _iter_elements(window, time.time() + _BUDGET_S):
        try:
            ctype = el.element_info.control_type or ""
            name = el.window_text() or ""
        except Exception:
            continue
        # A bare control-type request ("the text area") scores by type alone.
        if not needle:
            s = 0.85 if (wanted_type and ctype == wanted_type) else 0.0
        else:
            s = _score(needle, name)
            if wanted_type:
                s = s + 0.1 if ctype == wanted_type else s * 0.6
        if s > 0:
            scored.append((s, ctype, name, el))

    if not scored:
        return {"ok": False,
                "message": f"Nothing matching '{description}' on screen.",
                "candidates": _sample_names(window)}

    scored.sort(key=lambda t: t[0], reverse=True)
    best = scored[0]
    if len(scored) > 1 and (best[0] - scored[1][0]) < _AMBIGUITY_MARGIN \
            and abs(best[0] - scored[1][0]) >= 0:
        tied = [n for _s, _c, n, _e in scored[:4] if n]
        if len(set(tied)) > 1:
            return {"ok": False,
                    "message": f"'{description}' is ambiguous — {len(tied)} close matches.",
                    "candidates": tied}

    _s, ctype, name, el = best
    try:
        r = el.rectangle()
        mid = r.mid_point()
        return {"ok": True, "element_name": name, "control_type": ctype,
                "window_title": title, "window_is_dialog": _is_dialog(window),
                "rect": (r.left, r.top, r.right, r.bottom),
                "mid_point": (int(mid.x), int(mid.y))}
    except Exception as exc:
        return {"ok": False, "message": f"Found '{name}' but couldn't locate it ({exc}).",
                "candidates": []}


def _sample_names(window, limit: int = 8) -> list[str]:
    names = []
    for el in _iter_elements(window, time.time() + 1.0):
        try:
            n = el.window_text()
            ct = el.element_info.control_type
        except Exception:
            continue
        if n and ct in ("Button", "MenuItem", "TabItem", "Edit", "CheckBox", "Hyperlink"):
            names.append(n)
        if len(names) >= limit:
            break
    return names


# ============================ synthesis ============================

def _refocus_foreground(expected_title: str | None) -> bool:
    """True if the foreground window still matches expected (or no expectation)."""
    if not expected_title:
        return True
    try:
        import win32gui
        return expected_title.casefold() in _norm(win32gui.GetWindowText(
            win32gui.GetForegroundWindow()))
    except Exception:
        return True  # can't tell → don't block (best effort)


def _acquire_focus(win, expected_title: str | None, attempts: int = 4) -> bool:
    """Bring `win` to the foreground and confirm it. Retries because a
    fullscreen app (e.g. a game) can lose the first SetForegroundWindow race.
    Returns True only when the window is actually frontmost."""
    if win is None:
        return _refocus_foreground(expected_title)
    for i in range(attempts):
        try:
            win.set_focus()
        except Exception:
            try:
                win.restore()  # minimized windows reject set_focus until restored
                win.set_focus()
            except Exception:
                pass
        time.sleep(_FOCUS_SETTLE_S + 0.1 * i)  # back off a little each try
        if _refocus_foreground(expected_title):
            return True
    return _refocus_foreground(expected_title)


def _do_type(text: str) -> None:
    """Type text via pyautogui (handles Shift/case), else pywinauto unicode.

    NOTE: pydirectinput is deliberately NOT used for text — its write() maps
    only unshifted keys and silently drops uppercase/shifted characters
    ("Ship" -> "hip"). It stays the primary for press_keys/click (game keys),
    where that limitation doesn't apply.
    """
    if text.isascii():
        try:
            import pyautogui
            # 0.03s/key: at 0.01 the Shift for a second uppercase letter can
            # race and drop ("World" -> "world"); 0.03 was 3/3 reliable, send_keys 2/3.
            pyautogui.write(text, interval=0.03)
            return
        except Exception:
            pass
    from pywinauto.keyboard import send_keys
    send_keys(_escape_send_keys(text), with_spaces=True, pause=0.0)


def _escape_send_keys(text: str) -> str:
    # pywinauto send_keys treats {}()+^%~ specially — escape to literals.
    return re.sub(r"([{}()\[\]+^%~])", r"{\1}", text)


def type_text(text: str, window_hint: str | None = None) -> dict:
    """AUTO. Types into the focused window. NEVER sends Enter — newlines are
    stripped (submitting is a separate confirm-gated press_keys). Chunked,
    re-checking focus between chunks so a focus-steal aborts mid-word."""
    text = str(text if text is not None else "")
    had_newline = "\n" in text or "\r" in text
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")

    win, title = _target_window(window_hint)
    expected = title

    if not _acquire_focus(win, expected):
        return {"ok": False, "message": "Target window isn't focused — aborted before typing."}
    time.sleep(_PRE_INPUT_SETTLE_S)  # let the input queue catch up (first-key drop)

    typed = 0
    for i in range(0, len(text), _CHUNK):
        if not _refocus_foreground(expected):
            return {"ok": False,
                    "message": f"Focus changed after {typed} chars — stopped typing to avoid "
                               f"sending keys to the wrong window."}
        chunk = text[i:i + _CHUNK]
        _do_type(chunk)
        typed += len(chunk)
        time.sleep(0.01)

    note = " (newlines dropped — ask me to press Enter separately)" if had_newline else ""
    return {"ok": True, "message": f"Typed {typed} characters{note}.", "typed": text}


def press_keys(combo: str, window_hint: str | None = None) -> dict:
    """Press a key combo like 'ctrl+s' or 'enter'. Tier is decided upstream."""
    combo = str(combo or "").strip()
    if not combo:
        return {"ok": False, "message": "No key combo given."}
    win, title = _target_window(window_hint)
    if not _acquire_focus(win, title):
        return {"ok": False, "message": "Target window isn't focused — aborted."}
    time.sleep(_PRE_INPUT_SETTLE_S)
    if not _refocus_foreground(title):  # a fullscreen app may have re-grabbed focus
        return {"ok": False,
                "message": "Focus was taken by another window before the keypress — "
                           "aborted so the keys don't hit the wrong app."}
    try:
        import pydirectinput
        keys = [k.strip().lower() for k in combo.split("+") if k.strip()]
        if len(keys) == 1:
            pydirectinput.press(keys[0])
        else:
            for k in keys[:-1]:
                pydirectinput.keyDown(k)
            pydirectinput.press(keys[-1])
            for k in reversed(keys[:-1]):
                pydirectinput.keyUp(k)
        return {"ok": True, "message": f"Pressed {combo}."}
    except Exception:
        pass
    try:
        from pywinauto.keyboard import send_keys
        send_keys(_combo_to_send_keys(combo))
        return {"ok": True, "message": f"Pressed {combo}."}
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't press {combo}: {exc}"}


def _combo_to_send_keys(combo: str) -> str:
    mods = {"ctrl": "^", "control": "^", "alt": "%", "shift": "+", "win": "^{ESC}"}
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    prefix = "".join(mods.get(p, "") for p in parts if p in mods)
    key = next((p for p in parts if p not in mods), "")
    named = {"enter": "{ENTER}", "esc": "{ESC}", "escape": "{ESC}", "tab": "{TAB}",
             "delete": "{DELETE}", "backspace": "{BACKSPACE}", "space": " ",
             "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}",
             "home": "{HOME}", "end": "{END}"}
    return prefix + named.get(key, key)


def click(description: str, window_hint: str | None = None,
          expect_name: str | None = None) -> dict:
    """Resolve a description and click its center. Tier decided upstream; this
    fn assumes approval already happened for CONFIRM-tier targets. It RE-
    resolves at execution time (never reuses stale coordinates); if the caller
    passed the name that was approved, a fresh resolution to a different
    element aborts — the user approved a specific target, not a description."""
    r = resolve_target(description, window_hint=window_hint)
    if not r["ok"]:
        cands = r.get("candidates") or []
        extra = f" I can see: {', '.join(cands[:6])}." if cands else ""
        return {"ok": False, "message": r["message"] + extra}
    if expect_name is not None and _norm(r["element_name"]) != _norm(expect_name):
        return {"ok": False,
                "message": f"The target changed while waiting: approved '{expect_name}', "
                           f"now resolves to '{r['element_name']}'. Not clicking."}
    if not _refocus_foreground(r["window_title"]):  # don't click into a window that stole focus
        return {"ok": False,
                "message": "Focus moved to another window before the click — aborted."}
    x, y = r["mid_point"]
    try:
        import pydirectinput
        pydirectinput.moveTo(x, y)
        pydirectinput.click(x, y)
        return {"ok": True, "message": f"Clicked '{r['element_name']}'.",
                "element_name": r["element_name"]}
    except Exception:
        pass
    try:
        import pyautogui
        pyautogui.click(x, y)
        return {"ok": True, "message": f"Clicked '{r['element_name']}'.",
                "element_name": r["element_name"]}
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't click '{r['element_name']}': {exc}"}


# ============================ tier classification ============================

def _norm_combo(combo: str) -> str:
    parts = [p.strip().lower() for p in str(combo).split("+") if p.strip()]
    parts = ["ctrl" if p == "control" else p for p in parts]
    mods = [p for p in parts if p in _MODS]
    keys = [p for p in parts if p not in _MODS]
    order = {"ctrl": 0, "alt": 1, "shift": 2, "win": 3}
    mods.sort(key=lambda m: order.get(m, 9))
    return "+".join(mods + keys)


def _click_tier(element_name: str, is_dialog: bool) -> str:
    n = _norm(element_name)
    if "don't save" in n or "don’t save" in n:
        return "confirm"
    if _DESTRUCTIVE_RE.search(n):
        return "confirm"
    if is_dialog and _DIALOG_COMMIT_RE.search(n):
        return "confirm"
    return "auto"


def _combo_tier(combo: str) -> str:
    c = _norm_combo(combo)
    if c in _CONFIRM_COMBOS:
        return "confirm"
    if c in _AUTO_COMBOS:
        return "auto"
    if "+" not in c and c in _AUTO_KEYS:
        return "auto"
    return "confirm"  # unknown → fail closed


def _is_terminal(title: str) -> bool:
    t = _norm(title)
    return any(k in t for k in _TERMINAL_HINTS)


def classify_click(args: dict) -> dict:
    """Resolve the target so the tier reflects the REAL element, and the
    modal names it. Resolution failure → auto (the click fn re-resolves and
    fails cleanly without acting — no need to prompt for a no-op)."""
    desc = str(args.get("target", ""))
    window = args.get("window")
    r = resolve_target(desc, window_hint=window)
    if not r["ok"]:
        return {"tier": "auto", "description": f"Click '{desc}'", "expect_name": None}
    tier = _click_tier(r["element_name"], r.get("window_is_dialog", False))
    if _is_terminal(r["window_title"]):
        tier = "confirm"
    label = r["element_name"] or desc
    return {"tier": tier, "expect_name": r["element_name"],
            "description": f"Click '{label}' in '{r['window_title']}'"}


def classify_type(args: dict) -> dict:
    window = args.get("window")
    _win, title = _target_window(window)
    where = f"'{title}'" if title else "the focused window"
    tier = "confirm" if _is_terminal(title or "") else "auto"
    return {"tier": tier, "expect_name": None, "description": f"Type into {where}"}


def classify_press(args: dict) -> dict:
    combo = str(args.get("combo", ""))
    window = args.get("window")
    _win, title = _target_window(window)
    tier = "confirm" if _is_terminal(title or "") else _combo_tier(combo)
    label = _COMBO_LABELS.get(_norm_combo(combo), "")
    where = f" in '{title}'" if title else ""
    desc = f"Press {combo}" + (f" ({label})" if label else "") + where
    return {"tier": tier, "expect_name": None, "description": desc}


# ============================ verify (readback) ============================

def read_back_text(window_hint: str | None = None) -> str | None:
    """Text of the edit/document control in the target window, for verifying
    type_text. None when no such control exposes text."""
    win, _title = _target_window(window_hint)
    if win is None:
        return None
    try:
        for c in win.descendants():
            if c.element_info.control_type in _EDIT_TYPES:
                txt = c.window_text()
                if txt is not None:
                    return txt
    except Exception:
        return None
    return None
