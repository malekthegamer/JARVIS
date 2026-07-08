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
    marker = "hello world"
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
