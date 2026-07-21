"""Slice-2 primitive tests. Stage 2: capture + diff math. Stage 3 appends
launch_app / ui_tree coverage (incl. the hostile and mangled-name cases)."""
from __future__ import annotations

import numpy as np

from jarvis.primitives import screen


# ---------- stage 2: capture_screen + screenshot_diff ----------

def test_capture_screen_returns_fullscreen_array():
    img = screen.capture_screen()
    assert img.ndim == 3 and img.shape[2] == 3
    assert img.shape[0] > 100 and img.shape[1] > 100


def test_diff_identical_is_zero():
    a = np.zeros((200, 300, 3), dtype=np.uint8)
    assert screen.screenshot_diff(a, a.copy()) == 0.0


def test_diff_detects_synthetic_change():
    a = np.zeros((200, 300, 3), dtype=np.uint8)
    b = a.copy()
    b[:40, :, :] = 200  # top 20% of rows changed hard
    frac = screen.screenshot_diff(a, b)
    assert 0.1 < frac < 0.35, frac


def test_diff_below_pixel_threshold_ignored():
    """Sub-threshold noise (cursor blink antialiasing etc.) must not count."""
    a = np.full((200, 300, 3), 100, dtype=np.uint8)
    b = a + 5  # below default pixel threshold
    assert screen.screenshot_diff(a, b) == 0.0


def test_diff_shape_mismatch_reports_full_change():
    a = np.zeros((100, 100, 3), dtype=np.uint8)
    b = np.zeros((120, 100, 3), dtype=np.uint8)
    assert screen.screenshot_diff(a, b) == 1.0


def test_live_rapid_captures_mostly_static():
    # Min across three pairs: one pair can straddle a big transient repaint
    # (window animations from neighboring tests); three can't all do so.
    fracs = []
    for _ in range(3):
        a = screen.capture_screen()
        b = screen.capture_screen()
        fracs.append(screen.screenshot_diff(a, b))
    assert min(fracs) < 0.5, f"every capture pair differs hugely: {fracs} — diff is broken"


# ---------- stage 3: launch_app + ui_tree ----------

import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from jarvis.primitives import apps, ui_tree


def _kill_notepad():
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"],
                   capture_output=True)


def test_launch_notepad_and_window_present():
    _kill_notepad()
    time.sleep(0.5)
    try:
        result = apps.launch_app("notepad")
        assert result["ok"], result
        assert result["pid"]
        # 12s: Win11's UWP Notepad broker is slow to hand off under load
        deadline = time.time() + 12
        while time.time() < deadline:
            if ui_tree.window_present("Notepad"):
                break
            time.sleep(0.4)
        else:
            raise AssertionError("Notepad window never appeared in the UI tree")
    finally:
        _kill_notepad()


def test_mangled_names_resolve_to_notepad():
    """Realistic voice-input mangling must recover (resolution only — no spam
    of actual launches)."""
    for name in ("note pad", "notepad app", "Note Pad app"):
        path, _tried = apps.resolve_app(name)
        assert path and "notepad" in path.lower(), (name, path)


def test_window_present_for_process_notepad():
    """Slice-6 acceptance finding: apps like Spotify retitle their window to
    the playing track, so title-substring presence false-negatives. Presence
    must also be checkable by OWNING PROCESS name."""
    _kill_notepad()
    time.sleep(0.5)
    try:
        assert apps.launch_app("notepad")["ok"]
        deadline = time.time() + 12
        while time.time() < deadline:
            if ui_tree.window_present_for_process("notepad.exe"):
                break
            time.sleep(0.4)
        else:
            raise AssertionError("no window owned by notepad.exe appeared")
    finally:
        _kill_notepad()


def test_window_present_for_process_absent():
    assert ui_tree.window_present_for_process("xyzzy-nope-9000.exe") is False


def test_start_menu_shortcut_fallback(tmp_path, monkeypatch):
    """Slice-6 finding: some installers (Spotify's per-user desktop build)
    register NO App Paths key and aren't on PATH — only a Start Menu .lnk.
    The resolver must find those."""
    menu = tmp_path / "Programs"
    menu.mkdir()
    (menu / "FakeTunes.lnk").write_bytes(b"stub")
    exe = tmp_path / "FakeTunes.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(apps, "_START_MENU_DIRS", [str(menu)])
    monkeypatch.setattr(apps, "_lnk_target", lambda p: str(exe))
    assert apps._resolve_executable("faketunes") == str(exe)


def test_start_menu_shortcut_exact_match_only(tmp_path, monkeypatch):
    """No fuzzy grabbing: 'faketunes' must NOT match 'FakeTunes Deluxe.lnk'
    (fail closed — wrong-app launches are worse than a clean failure)."""
    menu = tmp_path / "Programs"
    menu.mkdir()
    (menu / "FakeTunes Deluxe.lnk").write_bytes(b"stub")
    monkeypatch.setattr(apps, "_START_MENU_DIRS", [str(menu)])
    monkeypatch.setattr(apps, "_lnk_target", lambda p: r"C:\x\y.exe")
    assert apps._resolve_executable("faketunes") is None


def test_spotify_resolves_via_start_menu_on_this_machine():
    """Machine-reality pin (the slice-6 acceptance blocker): Spotify here
    exposes ONLY a Start Menu shortcut — resolution must still find it."""
    import os
    lnk = os.path.join(os.environ.get("APPDATA", ""),
                       r"Microsoft\Windows\Start Menu\Programs\Spotify.lnk")
    if not os.path.exists(lnk):
        pytest.skip("Spotify not installed on this machine")
    path, _ = apps.resolve_app("spotify")
    assert path and path.lower().endswith("spotify.exe"), path


def test_nonexistent_app_fails_clean():
    result = apps.launch_app("xyzzy-not-an-app-9000")
    assert result["ok"] is False
    assert result["pid"] is None
    assert "xyzzy-not-an-app-9000" in result["message"]


def test_read_ui_tree_lists_real_windows():
    tree = ui_tree.read_ui_tree()
    assert isinstance(tree, str)
    assert "window:" in tree.lower(), tree[:200]


def test_ui_tree_com_threadpool_soak():
    """UIA/COM init failures in worker threads are INTERMITTENT — run on a
    fresh, never-initialized thread 8x consecutively (mimics the server
    threadpool), not once."""
    for i in range(8):
        with ThreadPoolExecutor(max_workers=1) as pool:
            windows = pool.submit(ui_tree.list_windows).result(timeout=20)
        assert isinstance(windows, list) and windows, f"iteration {i}: {windows!r}"


# ---------------------------------------------------------------- slice 29
def test_scroll_and_click_kind_exposed_in_schema():
    """The model must be able to SEE the new verbs: scroll as its own tool,
    and click's optional kind enum (single|double|right)."""
    from jarvis import primitives
    schemas = {s["name"]: s for s in primitives.tools_schema()}
    assert "scroll" in schemas
    sprops = schemas["scroll"]["parameters"]["properties"]
    assert {"direction", "amount", "window"} <= set(sprops)
    kind = schemas["click"]["parameters"]["properties"].get("kind")
    assert kind is not None, "click schema must expose 'kind'"
    assert set(kind.get("enum", [])) == {"single", "double", "right"}
