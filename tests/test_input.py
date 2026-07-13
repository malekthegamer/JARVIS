"""Slice-4 input-synthesis tests. Stage 1: target resolution + raw synthesis
against a live Notepad. The cardinal rule under test: an unresolvable or
ambiguous target NEVER yields coordinates — it fails and names alternatives."""
from __future__ import annotations

import subprocess
import time

import pytest

from jarvis.primitives import apps, input as jinput, ui_tree


def _kill_notepad():
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)


@pytest.fixture()
def notepad():
    _kill_notepad()
    time.sleep(0.5)
    assert apps.launch_app("notepad")["ok"]
    deadline = time.time() + 12
    while time.time() < deadline and not ui_tree.window_present("Notepad"):
        time.sleep(0.4)
    assert ui_tree.window_present("Notepad")
    time.sleep(0.5)
    yield
    _kill_notepad()


# ---------- resolution ----------

# Tests pass window_hint="Notepad" — the realistic agent path (it targets the
# window it opened) and deterministic regardless of what holds the foreground
# (this machine often has a fullscreen game up).

def test_resolve_text_area(notepad):
    r = jinput.resolve_target("the text area", window_hint="Notepad")
    assert r["ok"], r
    assert r["control_type"] in ("Edit", "Document", "Text")
    x, y = r["mid_point"]
    assert isinstance(x, int) and isinstance(y, int)


def test_resolve_missing_element_fails_with_candidates(notepad):
    r = jinput.resolve_target("the flux capacitor button", window_hint="Notepad")
    assert r["ok"] is False
    assert "mid_point" not in r
    assert r.get("candidates") is not None  # names what it DID see


def test_resolve_returns_no_coordinates_on_failure(notepad):
    r = jinput.resolve_target("nonexistent xyzzy widget 9000", window_hint="Notepad")
    assert r["ok"] is False
    assert "mid_point" not in r and "rect" not in r


# ---------- raw synthesis ----------

def test_type_text_lands_in_notepad(notepad):
    # Mixed case ON PURPOSE: pydirectinput silently drops uppercase, so an
    # all-lowercase marker would hide that regression ("Ship" -> "hip").
    marker = "Hello World 42"
    out = jinput.type_text(marker, window_hint="Notepad")
    assert out["ok"], out
    time.sleep(0.4)
    # read back via UIA: Notepad's Document control text == its content
    import pythoncom
    pythoncom.CoInitialize()
    from pywinauto import Desktop
    np = next(w for w in Desktop(backend="uia").windows() if "Notepad" in w.window_text())
    content = ""
    for c in np.descendants():
        if c.element_info.control_type in ("Document", "Edit"):
            content = c.window_text()
            break
    assert marker in content, f"typed text not found; readback={content!r}"


def test_press_keys_auto_combo_runs(notepad):
    jinput.type_text("select me", window_hint="Notepad")
    time.sleep(0.3)
    out = jinput.press_keys("ctrl+a", window_hint="Notepad")
    assert out["ok"], out


def test_type_text_strips_newlines(notepad):
    out = jinput.type_text("line one\nline two", window_hint="Notepad")
    assert out["ok"]
    assert "newline" in out["message"].lower() or "enter" in out["message"].lower()
    time.sleep(0.3)
    import pythoncom
    pythoncom.CoInitialize()
    from pywinauto import Desktop
    np = next(w for w in Desktop(backend="uia").windows() if "Notepad" in w.window_text())
    content = ""
    for c in np.descendants():
        if c.element_info.control_type in ("Document", "Edit"):
            content = c.window_text()
            break
    # both words present, but not separated by an actual newline we sent
    assert "line one" in content and "line two" in content


# ---------- stage 2: focus safety (monkeypatched — no reliance on real races) ----------

def test_type_aborts_when_window_not_focused(notepad, monkeypatch):
    """If focus can't be confirmed, type_text must abort before sending keys."""
    typed = []
    monkeypatch.setattr(jinput, "_refocus_foreground", lambda title: False)
    monkeypatch.setattr(jinput, "_do_type", lambda t: typed.append(t))
    out = jinput.type_text("secret keystrokes", window_hint="Notepad")
    assert out["ok"] is False
    assert "focus" in out["message"].lower()
    assert typed == [], "nothing may be typed when focus is unconfirmed"


def test_press_keys_aborts_when_not_focused(notepad, monkeypatch):
    monkeypatch.setattr(jinput, "_refocus_foreground", lambda title: False)
    out = jinput.press_keys("ctrl+s", window_hint="Notepad")
    assert out["ok"] is False
    assert "focus" in out["message"].lower()


def test_type_stops_at_chunk_boundary_on_focus_loss(notepad, monkeypatch):
    """Focus lost mid-type → stop at the next chunk boundary, not spray keys."""
    chunks = []
    monkeypatch.setattr(jinput, "_do_type", lambda t: chunks.append(t))
    # focus OK for acquire + first chunk, then stolen
    calls = {"n": 0}

    def flaky(_title):
        calls["n"] += 1
        return calls["n"] <= 1  # first chunk's guard passes; then focus is "stolen"

    monkeypatch.setattr(jinput, "_acquire_focus", lambda w, t, attempts=4: True)
    monkeypatch.setattr(jinput, "_refocus_foreground", flaky)
    out = jinput.type_text("x" * 100, window_hint="Notepad")  # 3 chunks of 40
    assert out["ok"] is False
    assert "focus changed" in out["message"].lower()
    assert len(chunks) == 1, f"typed {len(chunks)} chunks — should stop at boundary"


def test_click_aborts_when_window_closed(notepad):
    """Resolve, then the window vanishes → clean failure, no phantom click."""
    r = jinput.resolve_target("the text area", window_hint="Notepad")
    assert r["ok"]
    _kill_notepad()
    time.sleep(1.0)
    out = jinput.click("the text area", window_hint="Notepad")
    assert out["ok"] is False
    assert "mid_point" not in out


# ---------- stage 3: tier classifier (pure logic, no UIA) ----------

@pytest.mark.parametrize("name,is_dialog,expected", [
    ("Save", False, "confirm"),
    ("Send", False, "confirm"),
    ("Delete", False, "confirm"),
    ("Submit", False, "confirm"),
    ("Don't Save", False, "confirm"),
    ("Publish", False, "confirm"),
    ("Bold", False, "auto"),        # a formatting button is harmless
    ("File", False, "auto"),        # a menu is harmless to open
    ("Cut", False, "auto"),
    ("OK", True, "confirm"),        # OK in a DIALOG commits something
    ("OK", False, "auto"),          # "OK" as a plain label doesn't
    ("Yes", True, "confirm"),
    ("Continue", True, "confirm"),
])
def test_click_tier(name, is_dialog, expected):
    assert jinput._click_tier(name, is_dialog) == expected


@pytest.mark.parametrize("combo,expected", [
    ("ctrl+c", "auto"), ("ctrl+v", "auto"), ("ctrl+a", "auto"), ("ctrl+z", "auto"),
    ("tab", "auto"), ("up", "auto"), ("esc", "auto"), ("home", "auto"),
    ("enter", "confirm"), ("ctrl+enter", "confirm"), ("ctrl+s", "confirm"),
    ("alt+f4", "confirm"), ("ctrl+w", "confirm"), ("delete", "confirm"),
    ("ctrl+shift+p", "confirm"),   # unknown → fail closed
    ("f5", "confirm"),             # unknown → fail closed
])
def test_combo_tier(combo, expected):
    assert jinput._combo_tier(combo) == expected


def test_combo_tier_normalizes_modifier_order():
    assert jinput._combo_tier("a+ctrl") == jinput._combo_tier("ctrl+a") == "auto"


@pytest.mark.parametrize("title", ["Command Prompt", "Windows PowerShell",
                                   "cmd.exe", "Terminal"])
def test_terminal_backstop(title):
    assert jinput._is_terminal(title) is True


def test_non_terminal_window_not_flagged():
    assert jinput._is_terminal("Untitled - Notepad") is False


# ---------- verification follow-up: broadened destructive vocab + wiring ----------

@pytest.mark.parametrize("name", [
    "Erase", "Wipe", "Overwrite", "Replace All", "Reset", "Trash",
    "Move to Bin", "Unsend", "Deactivate account", "Unsubscribe",
])
def test_click_tier_broadened_destructive_words(name):
    """These were AUTO before the verification pass — now correctly gated."""
    assert jinput._click_tier(name, False) == "confirm"


# ---------- slice 16: vocabulary gaps found by the vision golden-set eval ----------
# The hard-benchmark measured `unsafe_auto=3/3` on a Print icon (correctly located
# and labelled, but classified AUTO — JARVIS would print without confirming), and
# a direct probe showed every non-English destructive verb classifying AUTO.
# Both are VOCABULARY gaps in the shared classifier, not mechanism gaps.

@pytest.mark.parametrize("name", [
    # German
    "Löschen", "Entfernen", "Senden", "Kaufen", "Speichern", "Bezahlen",
    # French
    "Supprimer", "Effacer", "Envoyer", "Acheter", "Enregistrer", "Payer",
    # Spanish / Portuguese / Italian
    "Eliminar", "Borrar", "Enviar", "Comprar", "Guardar", "Pagar",
    "Excluir", "Apagar", "Salvar", "Elimina", "Cancella", "Invia",
    # Dutch / Polish / Turkish
    "Verwijderen", "Verzenden", "Usuń", "Wyślij", "Sil", "Gönder",
    # Cyrillic
    "Удалить", "Отправить", "Купить",
])
def test_fastpath_non_english_destructive_names_confirm(name):
    """A German 'Löschen' button MUST gate exactly like an English 'Delete'.
    Before slice 16 every one of these classified AUTO — a delete that
    auto-clicked with no confirmation on any non-English UI."""
    assert jinput._click_tier(name, False) == "confirm"


@pytest.mark.parametrize("name", ["删除", "发送", "购买", "保存", "提交",
                                  "削除", "送信", "購入", "삭제", "전송"])
def test_fastpath_cjk_destructive_names_confirm(name):
    """CJK has no word boundaries, so `\\b` can never match it — these need
    substring matching. Pinned separately because it's a different mechanism."""
    assert jinput._click_tier(name, False) == "confirm"


@pytest.mark.parametrize("name", ["Print", "Print…", "Print document",
                                  "Drucken", "Imprimer", "Imprimir", "打印"])
def test_fastpath_print_is_committal_confirm(name):
    """MEASURED by the hard eval: the model labels a print icon correctly but
    called it safe → AUTO. Printing is not undoable (paper/ink), so it is
    committal and must gate."""
    assert jinput._click_tier(name, False) == "confirm"


@pytest.mark.parametrize("name", [
    "Binary", "Combine", "Reserved", "Silent mode", "Details", "Open",
    "Copy", "Undo", "Redo", "Zoom in", "Paste", "Bold", "Cancel", "Close",
    "Preferences", "Sign in with Google",  # 'sign' IS gated — see below
])
def test_fastpath_safe_words_still_auto(name):
    """No over-gating regression: the broadened vocabulary must not start firing
    on innocuous controls. ('Sil' must not match 'Silent'; 'Details' must not
    match any delete verb.)"""
    expected = "confirm" if name == "Sign in with Google" else "auto"
    assert jinput._click_tier(name, False) == expected


@pytest.mark.parametrize("name", ["Binary", "Combine", "Reserved", "Format Painter"])
def test_click_tier_no_false_positive_on_substrings(name):
    """Whole-word matching: 'bin' must not fire on 'Binary'/'Combine', and a
    benign 'Format Painter' toolbar button is... actually 'format' IS gated —
    verify the substring-only ones stay AUTO."""
    # 'Format Painter' contains the whole word 'format' → intentionally confirm.
    expected = "confirm" if name == "Format Painter" else "auto"
    assert jinput._click_tier(name, False) == expected


def _classify_click_with(monkeypatch, element_name, is_dialog=False, window="Some App"):
    def fake_resolve(desc, window_hint=None):
        return {"ok": True, "element_name": element_name, "control_type": "Button",
                "window_title": window, "window_is_dialog": is_dialog,
                "rect": (0, 0, 10, 10), "mid_point": (5, 5)}
    monkeypatch.setattr(jinput, "resolve_target", fake_resolve)
    return jinput.classify_click({"target": element_name, "window": window})


@pytest.mark.parametrize("label", ["Send", "Delete", "Submit", "Confirm", "Post"])
def test_classify_click_gates_destructive_buttons_end_to_end(monkeypatch, label):
    """Not just _click_tier in isolation — the whole classify_click path a
    real button click travels must return confirm."""
    info = _classify_click_with(monkeypatch, label)
    assert info["tier"] == "confirm"
    assert label.lower() in info["description"].lower()


def test_completing_the_save_is_itself_gated(monkeypatch):
    """Q1 guarantee: after Ctrl+S opens Save As, clicking the dialog's 'Save'
    button does NOT slip through as a silent auto-complete — it re-gates."""
    info = _classify_click_with(monkeypatch, "Save", is_dialog=True, window="Save As")
    assert info["tier"] == "confirm"


def test_safe_clicks_stay_auto_no_false_positive(monkeypatch):
    for label in ["Bold", "Italic", "Cut", "Copy", "Zoom in", "File"]:
        assert _classify_click_with(monkeypatch, label)["tier"] == "auto", label


def test_known_blind_spots_are_documented_not_secretly_working(monkeypatch):
    """HONEST pin of current limitations. If any of these ever flips to
    'confirm', update this test — it exists so a real gap can't hide.

    SLICE 16 UPDATE: the i18n blind spot this test used to pin is now CLOSED —
    'Enviar' and 'Löschen' correctly gate (see
    test_fastpath_non_english_destructive_names_confirm). The two remaining
    blind spots below are still real and deliberately NOT caught:
      - an icon-only button with an EMPTY accessible name (the fast path can't
        name it; classify_click hands off to the vision fallback instead)
      - 'OK'/'Yes' outside a UIA Dialog (dialog-scoped, to avoid over-prompting)
    """
    # closed by slice 16 — kept here so a regression would be loud
    assert _classify_click_with(monkeypatch, "Enviar")["tier"] == "confirm"
    assert _classify_click_with(monkeypatch, "Löschen")["tier"] == "confirm"
    # still-open, deliberate limits
    assert _classify_click_with(monkeypatch, "")["tier"] == "auto"
    assert _classify_click_with(monkeypatch, "OK", is_dialog=False)["tier"] == "auto"


def test_enter_and_ctrl_enter_gate_chat_submit():
    """Pressing Enter (send a chat message) must not slip through as AUTO."""
    assert jinput.classify_press({"combo": "enter", "window": "Discord"})["tier"] == "confirm"
    assert jinput.classify_press({"combo": "ctrl+enter", "window": "Slack"})["tier"] == "confirm"


# ---------- slice 5: vision fallback wiring (mocked vision) ----------

from jarvis.primitives import vision as jvision


def _fast_found(name, title="Untitled - Notepad", dialog=False):
    return lambda desc, window_hint=None: {
        "ok": True, "element_name": name, "control_type": "Button",
        "window_title": title, "window_is_dialog": dialog,
        "rect": (0, 0, 10, 10), "mid_point": (5, 5)}


def _fast_fail():
    return lambda desc, window_hint=None: {"ok": False, "message": "not found",
                                           "candidates": []}


def test_fast_path_wins_vision_not_called(monkeypatch):
    """Script 2: a text-labelled button resolves fast; vision NEVER runs."""
    monkeypatch.setattr(jinput, "resolve_target", _fast_found("File", dialog=False))
    calls = {"n": 0}
    monkeypatch.setattr(jvision, "locate_and_classify",
                        lambda d, window_hint=None: calls.__setitem__("n", calls["n"] + 1))
    info = jinput.classify_click({"target": "File", "window": "Notepad"})
    assert calls["n"] == 0, "vision must not run when the fast path names an element"
    assert info["tier"] == "auto" and "vision_point" not in info


def test_empty_name_resolution_triggers_vision(monkeypatch):
    monkeypatch.setattr(jinput, "resolve_target", _fast_found("   "))  # whitespace name
    calls = {"n": 0}
    monkeypatch.setattr(jvision, "locate_and_classify",
                        lambda d, window_hint=None: calls.__setitem__("n", calls["n"] + 1) or
                        {"ok": True, "point": (1, 1), "label": "bold", "tier": "auto",
                         "window_title": "App", "confidence": 0.9})
    jinput.classify_click({"target": "icon", "window": "App"})
    assert calls["n"] == 1, "an empty accessible name must fall through to vision"


def test_vision_destructive_is_confirm(monkeypatch):
    monkeypatch.setattr(jinput, "resolve_target", _fast_fail())
    monkeypatch.setattr(jvision, "locate_and_classify",
                        lambda d, window_hint=None: {"ok": True, "point": (160, 260),
                        "label": "delete item", "tier": "confirm",
                        "window_title": "IconPad", "confidence": 0.9})
    info = jinput.classify_click({"target": "the trash", "window": "IconPad"})
    assert info["tier"] == "confirm"
    assert info["vision_point"] == (160, 260)
    assert "delete item" in info["description"] and "visual" in info["description"].lower()


def test_vision_safe_is_auto(monkeypatch):
    monkeypatch.setattr(jinput, "resolve_target", _fast_fail())
    monkeypatch.setattr(jvision, "locate_and_classify",
                        lambda d, window_hint=None: {"ok": True, "point": (50, 50),
                        "label": "bold", "tier": "auto", "window_title": "IconPad",
                        "confidence": 0.95})
    info = jinput.classify_click({"target": "bold", "window": "IconPad"})
    assert info["tier"] == "auto" and info["vision_point"] == (50, 50)


def test_vision_terminal_backstop(monkeypatch):
    """A 'safe' vision label in a terminal window is still CONFIRM."""
    monkeypatch.setattr(jinput, "resolve_target", _fast_fail())
    monkeypatch.setattr(jvision, "locate_and_classify",
                        lambda d, window_hint=None: {"ok": True, "point": (10, 10),
                        "label": "clear", "tier": "auto", "window_title": "Command Prompt",
                        "confidence": 0.9})
    info = jinput.classify_click({"target": "x", "window": "cmd"})
    assert info["tier"] == "confirm"


def test_vision_no_match_fails_open_to_clean_failure(monkeypatch):
    monkeypatch.setattr(jinput, "resolve_target", _fast_fail())
    monkeypatch.setattr(jvision, "locate_and_classify",
                        lambda d, window_hint=None: {"ok": False,
                        "reason": "couldn't find it, even visually"})
    info = jinput.classify_click({"target": "ghost", "window": "IconPad"})
    assert info["tier"] == "auto" and info.get("vision_failed")
    assert "vision_point" not in info


# ---------- click(point=) execution guards ----------

class _FakeRect:
    def __init__(self, l, t, r, b):
        self.left, self.top, self.right, self.bottom = l, t, r, b


class _FakeWin:
    def __init__(self, rect):
        self._r = rect
    def rectangle(self):
        return self._r
    def set_focus(self):
        pass
    def restore(self):
        pass


def _wire_point_click(monkeypatch, rect, foreground=True, element=True):
    monkeypatch.setattr(jinput, "_target_window", lambda wh: (_FakeWin(rect), "App"))
    monkeypatch.setattr(jinput, "_acquire_focus", lambda w, t, attempts=4: True)
    monkeypatch.setattr(jinput, "_refocus_foreground", lambda t: foreground)
    monkeypatch.setattr(jinput, "_element_present_at", lambda p: element)


def test_click_point_rejects_out_of_bounds(monkeypatch):
    _wire_point_click(monkeypatch, _FakeRect(100, 200, 150, 250))
    out = jinput.click("x", window_hint="App", point=(500, 500))
    assert out["ok"] is False and "outside" in out["message"].lower()


def test_click_point_rejects_when_no_element_there(monkeypatch):
    _wire_point_click(monkeypatch, _FakeRect(0, 0, 1000, 1000), element=False)
    out = jinput.click("x", window_hint="App", point=(50, 50))
    assert out["ok"] is False and "clickable" in out["message"].lower()


def test_click_point_aborts_on_focus_loss(monkeypatch):
    _wire_point_click(monkeypatch, _FakeRect(0, 0, 1000, 1000), foreground=False)
    out = jinput.click("x", window_hint="App", point=(50, 50))
    assert out["ok"] is False


def test_click_point_success_clicks_coords(monkeypatch):
    import sys, types
    _wire_point_click(monkeypatch, _FakeRect(0, 0, 1000, 1000))
    clicked = {}
    fake = types.SimpleNamespace(moveTo=lambda x, y: clicked.update(mv=(x, y)),
                                 click=lambda x, y: clicked.update(cl=(x, y)))
    monkeypatch.setitem(sys.modules, "pydirectinput", fake)
    out = jinput.click("x", window_hint="App", point=(50, 60), expect_label="bold")
    assert out["ok"] and clicked["cl"] == (50, 60)
    assert "bold" in out["message"]


def test_click_point_window_gone(monkeypatch):
    monkeypatch.setattr(jinput, "_target_window", lambda wh: (None, None))
    out = jinput.click("x", window_hint="App", point=(50, 60))
    assert out["ok"] is False
