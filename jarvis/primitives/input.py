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
# English committal/destructive verbs. Whole-word matched (\b…\b) so "binary"
# won't trip "bin" and "combine" won't trip "bin". Errs toward over-prompting;
# see the honest blind-spot list in classify_click's tests (non-English and
# icon-only labels are NOT covered — that needs the vision fallback slice).
_DESTRUCTIVE_RE = re.compile(
    r"\b(send|unsend|submit|confirm|delete|erase|wipe|remove|trash|bin|"
    r"save|overwrite|replace|reset|discard|buy|purchase|pay|order|post|"
    r"publish|reply|share|sign|apply|install|uninstall|deactivate|"
    # slice 16: 'print' was MEASURED unsafe by the vision golden-set eval — the
    # model located and labelled a Print icon correctly but called it safe, so it
    # classified AUTO. Printing is not undoable (paper/ink) ⇒ committal.
    r"unsubscribe|format|empty|print|"
    # ---- slice 16 i18n. Same MECHANISM (whole-word match on the resolved name),
    # widened VOCABULARY. Before this, "Löschen"/"Supprimer"/"Eliminar" all
    # classified AUTO — a delete button that auto-clicked on any non-English UI.
    # Direction is fail-safe: an entry can only move a control toward CONFIRM.
    r"löschen|loeschen|entfernen|senden|kaufen|speichern|bezahlen|bestellen|drucken|"
    r"supprimer|effacer|envoyer|acheter|enregistrer|payer|commander|imprimer|"
    r"eliminar|borrar|enviar|comprar|guardar|pagar|excluir|apagar|salvar|imprimir|"
    r"elimina|cancella|invia|compra|salva|paga|stampa|"
    r"verwijderen|verzenden|kopen|opslaan|"
    r"usuń|usun|wyślij|wyslij|kup|zapisz|"
    r"sil|gönder|gonder|satın|satin|kaydet|"
    r"удалить|отправить|купить|сохранить|оплатить|отправка"
    r")\b")

# CJK/Korean have NO word boundaries, so `\b` can never match them — these are
# matched as substrings instead (a different mechanism, same vocabulary intent).
_CJK_DESTRUCTIVE = (
    "删除", "刪除", "移除", "清空", "发送", "發送", "提交", "购买", "購買",
    "保存", "儲存", "另存", "打印", "列印", "支付", "付款", "购物",
    "削除", "送信", "購入", "印刷", "保存",
    "삭제", "전송", "구매", "저장", "인쇄", "제출",
)


def is_committal_name(name: str) -> bool:
    """The ONE committal/destructive vocabulary check — English + i18n + CJK.

    Shared by the fast path (_click_tier, on the resolved UIA name) and the vision
    fallback (vision._tier_for, on the model's label), so the two can never drift
    apart. Fail-safe: a hit only ever moves a control toward CONFIRM."""
    n = _norm(name)
    if _DESTRUCTIVE_RE.search(n):
        return True
    return any(w in n for w in _CJK_DESTRUCTIVE)
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

# Slice 38: how much payload the CONFIRM modal's monospace box will carry.
# Capped so a huge paste can't blow out the layout; the elision states the
# real length so the cap is never mistaken for the whole payload.
_MAX_CONFIRM_PAYLOAD = 500


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
        # Slice 21: resolve the HWND fast (win32) and wrap that handle — the
        # same hybrid _foreground_window uses. Replaces a ~10 s double UIA
        # enumeration (find title + re-enumerate to match it) that also risked
        # a win32-vs-UIA title mismatch. The handle is an unambiguous token.
        hwnd, title = jwindows.find_window(window_hint)
        if hwnd is None:
            return None, None
        try:
            _co_init()
            from pywinauto import Desktop
            win = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
            return win, title
        except Exception:
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


def _sanitize_typed(text) -> str:
    """The string type_text will ACTUALLY send: newlines collapse to spaces,
    because typing never submits (that's a separate confirm-gated press_keys).

    Slice 38: classify_type shows this same string in the confirm box, so the
    two can't drift. Echoing the raw argument instead would put text in the
    modal that differs from what lands in the window — a paraphrase, which is
    the one thing the gate exists to prevent."""
    s = str(text if text is not None else "")
    return s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _for_confirm_box(text: str) -> str:
    """Length-cap a payload for the modal's monospace box (slice 38)."""
    s = str(text or "")
    if len(s) <= _MAX_CONFIRM_PAYLOAD:
        return s
    return f"{s[:_MAX_CONFIRM_PAYLOAD]}… [truncated, {len(s)} chars total]"


# --- what JARVIS last typed, per window (slice 38) ---------------------------
#
# press_keys' args don't carry the payload — an earlier AUTO type_text put it
# there — so a submit CONFIRM had nothing to show but the keystroke. This
# records what JARVIS itself sent, which is definitionally accurate about
# JARVIS's own action.
#
# Stage-0 Probe B ruled out reading the field live at confirm time: UIA DOES
# return text from a real edit control, but returned text that did not match
# what had just been typed — a modal filled from that would show the WRONG
# text to approve, which is worse than showing none.
#
# In-memory and process-scoped, same posture as the undo stack: a restart
# forgets, and that is disclosed rather than hidden.
_LAST_TYPED_TTL_S = 120.0
_LAST_TYPED_MAX = 8
_last_typed: dict[str, tuple[str, float]] = {}


def _record_typed(title: str | None, text: str) -> None:
    """Remember what was typed where. Bounded — this is a display hint, not
    state anything depends on, so overflow clears rather than evicting."""
    key = (title or "").strip().lower()
    if not key or not text:
        return
    if len(_last_typed) >= _LAST_TYPED_MAX and key not in _last_typed:
        _last_typed.clear()
    _last_typed[key] = (text, time.time())


def _recall_typed(title: str | None) -> tuple[str, float] | None:
    """(text, age_seconds) for this window, or None if nothing/too old.
    Expiry matters: showing a payload from ten minutes ago would actively
    mislead the person approving."""
    key = (title or "").strip().lower()
    entry = _last_typed.get(key) if key else None
    if entry is None:
        return None
    text, when = entry
    age = time.time() - when
    if age > _LAST_TYPED_TTL_S:
        _last_typed.pop(key, None)
        return None
    return text, age


def type_text(text: str, window_hint: str | None = None) -> dict:
    """AUTO. Types into the focused window. NEVER sends Enter — newlines are
    stripped (submitting is a separate confirm-gated press_keys). Chunked,
    re-checking focus between chunks so a focus-steal aborts mid-word."""
    raw = str(text if text is not None else "")
    had_newline = "\n" in raw or "\r" in raw
    text = _sanitize_typed(raw)

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

    _record_typed(expected, text)  # slice 38: so a follow-up submit can show it
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


def _element_present_at(point) -> bool:
    """True if a visible, enabled UIA element is at `point` (execution-time
    hit-test for vision clicks — catches coords that map to nothing)."""
    _co_init()
    try:
        from pywinauto import Desktop
        el = Desktop(backend="uia").from_point(int(point[0]), int(point[1]))
        return bool(el.is_visible() and el.is_enabled())
    except Exception:
        return False


def _move_cursor(x: int, y: int) -> None:
    """Slice 22: eased glide to (x, y) — PURELY cosmetic. The landing pixel
    is byte-identical to the old instant jump (the final exact moveTo always
    runs), total added time is capped by input.cursor_move_max_ms (200), and
    any failure inside the glide falls through to the plain jump — motion
    must never break a click. input.smooth_cursor=false restores teleport."""
    import pydirectinput
    x, y = int(x), int(y)
    try:
        from jarvis.core.settings_store import settings
        if settings.get("input.smooth_cursor", True):
            import pyautogui
            pos = pyautogui.position()
            dist = ((x - pos.x) ** 2 + (y - pos.y) ** 2) ** 0.5
            if dist > 4:
                steps = max(8, min(25, int(dist / 40)))
                total_s = min(float(settings.get("input.cursor_move_max_ms", 200)),
                              60 + dist * 0.15) / 1000.0
                pause = total_s / steps
                for i in range(1, steps):
                    t = i / steps
                    e = 1 - (1 - t) ** 2          # ease-out: fast start, soft landing
                    pydirectinput.moveTo(int(pos.x + (x - pos.x) * e),
                                         int(pos.y + (y - pos.y) * e),
                                         _pause=False)
                    time.sleep(pause)
    except Exception:
        pass  # cosmetic layer only — fall through to the exact jump
    try:
        pydirectinput.moveTo(x, y, _pause=False)
    except TypeError:
        pydirectinput.moveTo(x, y)  # a moveTo without _pause (incl. test fakes)


# Click kinds (slice 29). Maps to the identically-named pydirectinput/pyautogui
# method — both libraries expose click/doubleClick/rightClick.
CLICK_KINDS = {"single": "click", "double": "doubleClick", "right": "rightClick"}
_CLICK_VERB = {"single": "Clicked", "double": "Double-clicked", "right": "Right-clicked"}


def _click_xy(x: int, y: int, label: str, kind: str = "single") -> dict:
    method = CLICK_KINDS.get(kind)
    if method is None:                    # fail closed — never guess a click kind
        return {"ok": False,
                "message": f"Unknown click kind '{kind}'. "
                           f"Allowed: {', '.join(sorted(CLICK_KINDS))}."}
    verb = _CLICK_VERB[kind]
    note = (" A context menu is now open — click the item you want, or press "
            "Escape to dismiss it." if kind == "right" else "")
    try:
        import pydirectinput
        _move_cursor(x, y)
        getattr(pydirectinput, method)(x, y)
        return {"ok": True, "message": f"{verb} {label}.{note}"}
    except Exception:
        pass
    try:
        import pyautogui
        getattr(pyautogui, method)(x, y)
        return {"ok": True, "message": f"{verb} {label}.{note}"}
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't {kind}-click at ({x},{y}): {exc}"}


def _click_at_point(point, window_hint: str | None, expect_label: str | None,
                    kind: str = "single") -> dict:
    """Click a vision-provided point, with the same safety re-checks as a text
    click: window focused, point still inside the window, a real element there.
    Never reuses coords blind — every guard must pass first."""
    win, title = _target_window(window_hint)
    if win is None:
        return {"ok": False, "message": "the target window is gone — not clicking."}
    if not _acquire_focus(win, title):
        return {"ok": False, "message": "couldn't focus the target window — not clicking."}
    try:
        r = win.rectangle()
        if not (r.left <= point[0] <= r.right and r.top <= point[1] <= r.bottom):
            return {"ok": False,
                    "message": "the visual target is outside the window now — not clicking."}
    except Exception:
        pass
    if not _refocus_foreground(title):
        return {"ok": False, "message": "focus moved before the click — aborted."}
    if not _element_present_at(point):
        return {"ok": False,
                "message": "nothing clickable is at that spot anymore — not clicking."}
    # SLICE 17 — the last guard: is the control ACTUALLY here the one that was
    # approved? Vision can label correctly but point at the NEIGHBOUR (measured
    # slice 16), which would let the CONFIRM modal name one control while the click
    # lands on another. Reads a FRESH screenshot at click time, so a window that
    # changed since approval also fails here. Mismatch => refuse, never click, and
    # NEVER auto-retry or re-gate: a retry is a new action needing a fresh CONFIRM.
    from jarvis.primitives import vision  # function-local: avoids the import cycle
    v = vision.verify_point(point, window_hint, expect_label or "")
    if not v.get("verified"):
        return {"ok": False, "message": v.get("reason") or
                "couldn't confirm what's at that point — not clicking."}
    return _click_xy(int(point[0]), int(point[1]), expect_label or "the element", kind)


def click(description: str, window_hint: str | None = None,
          expect_name: str | None = None, point=None,
          expect_label: str | None = None, kind: str = "single") -> dict:
    """Click an element. `kind` ∈ single|double|right (slice 29) — never
    weakens the tier (classify_click decides from the resolved NAME). Two paths:
    - point given (vision fallback): click those coords after re-verifying
      focus + bounds + a real element is there (never reuse coords blind).
    - otherwise (fast path): resolve the description by text and click its
      centre, RE-resolving at execution time; if the caller passed the
      approved name, a fresh resolution to a different element aborts."""
    if kind not in CLICK_KINDS:                # fail closed before any action
        return {"ok": False,
                "message": f"Unknown click kind '{kind}'. "
                           f"Allowed: {', '.join(sorted(CLICK_KINDS))}."}
    if point is not None:
        return _click_at_point(point, window_hint, expect_label, kind)
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
    out = _click_xy(x, y, f"'{r['element_name']}'", kind)
    if out["ok"]:
        out["element_name"] = r["element_name"]
    return out


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
    if is_committal_name(element_name):  # English + i18n + CJK (slice 16)
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
    """Decide the tier for a click and locate its target.

    FAST PATH (primary, unchanged): the UIA text resolver. If it names an
    element, classify by text and we're done — vision never runs.

    FALLBACK: if the fast path can't identify a NAMED element (not found,
    ambiguous, or an icon with an empty accessible name), hand off to the
    vision resolver. The vision tier runs through the SAME classifier inputs
    (destructive regex + terminal backstop) unioned with vision's own risk —
    a second entrance to the gate, never a bypass."""
    desc = str(args.get("target", ""))
    window = args.get("window")

    r = resolve_target(desc, window_hint=window)
    if r["ok"] and (r["element_name"] or "").strip():
        tier = _click_tier(r["element_name"], r.get("window_is_dialog", False))
        if _is_terminal(r["window_title"]):
            tier = "confirm"
        return {"tier": tier, "expect_name": r["element_name"],
                "description": f"Click '{r['element_name']}' in '{r['window_title']}'"}

    # Fast path couldn't name a target → vision fallback.
    from jarvis.primitives import vision
    v = vision.locate_and_classify(desc, window_hint=window)
    if not v["ok"]:
        # Can't locate even visually. AUTO tier + no point → the click fn
        # reports the failure cleanly; no pointless CONFIRM for a no-op.
        return {"tier": "auto", "description": f"Click '{desc}'",
                "expect_name": None, "vision_failed": v.get("reason", "not found")}
    tier = "confirm" if _is_terminal(v["window_title"]) else v["tier"]
    return {"tier": tier, "expect_name": None,
            "vision_point": v["point"], "vision_label": v["label"],
            "description": f"Click '{v['label']}' (identified visually) in '{v['window_title']}'"}


# Scroll directions (slice 29): KEYBOARD paging, not synthetic mouse-wheel.
# Stage-0 probe finding — a synthetic wheel (pyautogui.scroll / mouse_event
# WHEEL / WM_MOUSEWHEEL) is UNRELIABLE on Win11's WinUI apps (moved once, then
# nothing, on real Notepad); PageUp/PageDown into the focused control moved the
# view on every press (4/4, both directions). So scroll pages the focused
# scroll container. Vertical only — keyboard has no clean horizontal analog;
# left/right fail closed honestly. `amount` = number of page steps.
_SCROLL_DIRS = {"up": "pageup", "down": "pagedown"}


def scroll(direction: str, amount=3, window_hint: str | None = None) -> dict:
    """Page the focused scroll container up/down (PageUp/PageDown). AUTO.
    Fail-closed on an unknown/horizontal direction, a non-numeric amount, or a
    window that can't be resolved/focused (never scroll whatever happens to be
    focused). `amount` = page steps, clamped 1..input.scroll_max_notches. Never
    raises. Returns the window `bbox` so the executor wrapper can verify the
    screen actually moved by diffing that region."""
    direction = str(direction or "").strip().lower()
    key = _SCROLL_DIRS.get(direction)
    if key is None:
        extra = (" — I can only scroll up or down, not sideways"
                 if direction in ("left", "right") else "")
        return {"ok": False,
                "message": f"Unknown scroll direction '{direction}'. "
                           f"Allowed: up, down.{extra}"}
    try:
        n = int(amount)
    except (TypeError, ValueError):
        return {"ok": False, "message": f"Scroll amount '{amount}' isn't a number."}
    from jarvis.core.settings_store import settings
    cap = int(settings.get("input.scroll_max_notches", 20))
    clamped = max(1, min(abs(n), cap))
    note = "" if clamped == abs(n) else f" (clamped from {n} — max {cap} steps)"

    win, title = _target_window(window_hint)
    if win is None:
        return {"ok": False,
                "message": "Couldn't find the window to scroll — not scrolling."}
    if not _acquire_focus(win, title) or not _refocus_foreground(title):
        return {"ok": False,
                "message": "Couldn't focus the target window — not scrolling."}
    bbox = None
    try:
        r = win.rectangle()
        bbox = (r.left, r.top, r.right, r.bottom)
    except Exception:
        pass
    # Page the FOCUSED control (a synthetic wheel is unreliable on WinUI — see
    # _SCROLL_DIRS). pydirectinput.press is the same key path type_text/press use.
    try:
        import pydirectinput
        for _ in range(clamped):
            pydirectinput.press(key)
            time.sleep(0.03)
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't scroll: {exc}"}
    return {"ok": True, "bbox": bbox,
            "message": f"Scrolled {direction} {clamped} step(s){note} "
                       f"in '{title or 'the window'}'."}


def classify_type(args: dict) -> dict:
    """Slice 38: `command` carries the VERBATIM text into the modal's monospace
    box. Before this, a terminal-bound type confirmed as `Type into 'Windows
    PowerShell'` — naming the window and hiding the payload, so it was approved
    blind. Description is unchanged (additive), and the payload is the
    _sanitize_typed form so the box matches what actually gets typed."""
    window = args.get("window")
    _win, title = _target_window(window)
    where = f"'{title}'" if title else "the focused window"
    tier = "confirm" if _is_terminal(title or "") else "auto"
    return {"tier": tier, "expect_name": None,
            "description": f"Type into {where}",
            "command": _for_confirm_box(_sanitize_typed(args.get("text")))}


def classify_press(args: dict) -> dict:
    """Tier for a key combo. NOTE on the Save-As boundary (deliberate for this
    slice): approving Ctrl+S gates and sends the KEYPRESS only. For an already-
    named file that keypress IS the irreversible write, so gating it is correct.
    For a new/untitled file it opens a Save As dialog — the code stops there and
    does NOT auto-complete it. Completing the save means the model separately
    clicks the dialog's 'Save' button, and that click is INDEPENDENTLY confirm-
    gated (classify_click: 'save' is destructive). So there is no silent auto-
    complete of the irreversible step — every path to disk crosses the gate."""
    combo = str(args.get("combo", ""))
    window = args.get("window")
    _win, title = _target_window(window)
    tier = "confirm" if _is_terminal(title or "") else _combo_tier(combo)
    label = _COMBO_LABELS.get(_norm_combo(combo), "")
    where = f" in '{title}'" if title else ""
    desc = f"Press {combo}" + (f" ({label})" if label else "") + where
    out = {"tier": tier, "expect_name": None, "description": desc}
    if tier == "confirm" and label == "submit":
        # Slice 38: a submit commits whatever is sitting in that window, so show
        # it. SUBMIT combos only — alt+f4/ctrl+w/delete don't commit typed text,
        # and attaching it there would be misleading rather than informative.
        got = _recall_typed(title)
        if got is None:
            out["command"] = "(JARVIS has not typed into this window)"
        else:
            text, age = got
            out["command"] = (f"JARVIS typed this {int(age)}s ago:\n"
                              f"{_for_confirm_box(text)}")
    return out


# ============================ verify (readback) ============================

def _edit_texts(window_hint: str | None) -> list[str]:
    """Every edit/document control's text in the target window. [] on any fault
    — this is a verifier, and a verifier that raises is worse than one that
    reports nothing."""
    win, _title = _target_window(window_hint)
    if win is None:
        return []
    out: list[str] = []
    try:
        for c in win.descendants():
            if c.element_info.control_type in _EDIT_TYPES:
                txt = c.window_text()
                if txt is not None:
                    out.append(txt)
    except Exception:
        return out
    return out


def read_back_text(window_hint: str | None = None, want: str = "",
                   timeout_s: float = 1.5) -> str | None:
    """Text of the edit/document control in the target window, for verifying
    type_text. None when no such control exposes text.

    SLICE 68. This used to do ONE pass and return the FIRST edit control it
    found, which made JARVIS report "control doesn't expose text — couldn't
    confirm" about typing that had demonstrably worked (caught in the slice-65
    gate: 20 characters really went in). Two causes, both fixed here:

      * Win11's WinUI Notepad updates its UIA text ASYNCHRONOUSLY, so an
        immediate read often finds nothing -> poll until `timeout_s`.
      * A window with a search box or status field exposes several edit
        controls, and the first is not the one that was typed into -> when we
        know what was typed, prefer the control that CONTAINS it.

    Returning None still means "genuinely could not read", so an honest
    "couldn't confirm" survives. Understating success is a smaller sin than
    claiming one that did not happen.
    """
    deadline = time.time() + max(0.0, float(timeout_s))
    best: str | None = None
    while True:
        texts = _edit_texts(window_hint)
        if want:
            for t in texts:
                if want in t:
                    return t
        if texts:
            # Richest control seen so far — the fallback when `want` never
            # shows up, and the whole answer when the caller didn't pass one.
            candidate = max(texts, key=len)
            if best is None or len(candidate) > len(best):
                best = candidate
            if not want:
                return best
        if time.time() >= deadline:
            return best
        time.sleep(0.15)
