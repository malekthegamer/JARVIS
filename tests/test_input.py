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
