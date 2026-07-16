"""Slice-3 stage-2 tests: the raw CONFIRM-tier primitive functions (pre-gate).
delete_file must be provably caged in data/agent_files/; close_window must
close exactly what it names and fail clean on no match."""
from __future__ import annotations

import subprocess
import time

import pytest

from jarvis import config
from jarvis.primitives import apps, files, ui_tree, windows


# ---------- delete_file ----------

@pytest.fixture()
def workspace_file():
    files.AGENT_FILES_DIR.mkdir(parents=True, exist_ok=True)
    f = files.AGENT_FILES_DIR / "test.txt"
    f.write_text("delete me", encoding="utf-8")
    yield f
    f.unlink(missing_ok=True)


def test_delete_file_happy_path(workspace_file):
    result = files.delete_file("test.txt")
    assert result["ok"] is True, result
    assert not workspace_file.exists()


@pytest.mark.parametrize("attack", [
    "../evil.txt",
    "..\\..\\.env",
    "sub/../../evil.txt",
    "....//test.txt",
])
def test_delete_file_traversal_rejected(workspace_file, attack):
    result = files.delete_file(attack)
    assert result["ok"] is False, (attack, result)
    assert "workspace" in result["message"].lower()
    assert workspace_file.exists()  # nothing was touched
    assert config.ENV_FILE.exists()  # and definitely not the secrets


def test_delete_file_absolute_path_rejected(workspace_file):
    result = files.delete_file(str(workspace_file.resolve()))
    assert result["ok"] is False
    assert workspace_file.exists()


def test_delete_file_missing_is_clean():
    result = files.delete_file("no-such-file-xyzzy.txt")
    assert result["ok"] is False
    assert "no-such-file-xyzzy.txt" in result["message"]


def test_delete_file_directory_rejected():
    sub = files.AGENT_FILES_DIR / "subdir"
    sub.mkdir(parents=True, exist_ok=True)
    try:
        result = files.delete_file("subdir")
        assert result["ok"] is False
        assert sub.exists()
    finally:
        sub.rmdir()


def test_delete_file_describe_names_target(workspace_file):
    desc = files.describe_delete({"name": "test.txt"})
    assert "test.txt" in desc and "workspace" in desc.lower()


# ---------- close_window ----------

def _kill_notepad():
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)


@pytest.fixture()
def notepad():
    _kill_notepad()
    time.sleep(0.5)
    result = apps.launch_app("notepad")
    assert result["ok"], result
    # 12s: Win11's UWP Notepad broker is slow to hand off under load
    deadline = time.time() + 12
    while time.time() < deadline and not ui_tree.window_present("Notepad"):
        time.sleep(0.4)
    assert ui_tree.window_present("Notepad"), "Notepad never appeared"
    yield
    _kill_notepad()


def test_close_window_closes_notepad(notepad):
    result = windows.close_window("notepad")
    assert result["ok"] is True, result
    deadline = time.time() + 5
    while time.time() < deadline and ui_tree.window_present("Notepad"):
        time.sleep(0.4)
    assert not ui_tree.window_present("Notepad")


def test_close_window_describe_names_actual_title(notepad):
    desc = windows.describe_close({"title": "notepad"})
    assert desc is not None
    assert "notepad" in desc.lower()


def test_close_window_no_match_is_clean():
    result = windows.close_window("xyzzy-window-that-does-not-exist")
    assert result["ok"] is False
    assert "xyzzy-window-that-does-not-exist" in result["message"]


def test_close_window_describe_none_when_no_match():
    assert windows.describe_close({"title": "xyzzy-window-that-does-not-exist"}) is None


# ---------- slice 6 acceptance finding: process-name window matching ----------

def test_find_window_title_matches_by_owning_process(monkeypatch):
    """Spotify retitles its window to the playing track, so hint 'spotify'
    stops matching by title mid-chain and every targeted action breaks.
    A window must also match when its OWNING PROCESS is '<hint>.exe'.

    Slice 21: _enum_windows now carries the HWND too (title, pid, hwnd) so
    the resolver can hand a handle to pywinauto instead of re-enumerating."""
    from jarvis.primitives import windows as jwindows

    monkeypatch.setattr(jwindows, "_enum_windows",
                        lambda: [("Kendrick Lamar - Not Like Us", 4242, 111),
                                 ("Untitled - Notepad", 777, 222)])

    class FakeProc:
        def __init__(self, pid):
            self._name = {4242: "Spotify.exe", 777: "Notepad.exe"}[pid]
        def name(self):
            return self._name

    import psutil
    monkeypatch.setattr(psutil, "Process", FakeProc)

    # find_window_title contract preserved (returns just the title string).
    assert jwindows.find_window_title("spotify") == "Kendrick Lamar - Not Like Us"
    assert jwindows.find_window_title("notepad") == "Untitled - Notepad"
    assert jwindows.find_window_title("xyzzy-nothing") is None

    # find_window returns (hwnd, title) with the SAME 3-pass precedence, so
    # the input resolver can wrap the handle directly.
    assert jwindows.find_window("spotify") == (111, "Kendrick Lamar - Not Like Us")
    assert jwindows.find_window("notepad") == (222, "Untitled - Notepad")
    assert jwindows.find_window("xyzzy-nothing") == (None, None)


# ---------- slice 8 stage 1: search_files (AUTO, caged, read-only) ----------

import os
import time as _t

import pytest as _pytest

from jarvis.primitives import files as _files


@_pytest.fixture()
def tmp_workspace(tmp_path, monkeypatch):
    ws = tmp_path / "agent_files"
    ws.mkdir()
    monkeypatch.setattr(_files, "AGENT_FILES_DIR", ws)
    return ws


def test_search_finds_by_name(tmp_workspace):
    (tmp_workspace / "invoice_2026.pdf").write_text("x")
    (tmp_workspace / "notes.txt").write_text("y")
    r = _files.search_files(query="invoice")
    assert r["ok"] and len(r["matches"]) == 1
    assert r["matches"][0]["name"] == "invoice_2026.pdf"
    assert "invoice_2026.pdf" in r["message"]


def test_search_by_ext_and_age(tmp_workspace):
    fresh = tmp_workspace / "fresh.pdf"
    fresh.write_text("new")
    old = tmp_workspace / "old.pdf"
    old.write_text("old")
    stale = _t.time() - 3 * 86400
    os.utime(old, (stale, stale))
    (tmp_workspace / "fresh.txt").write_text("t")
    r = _files.search_files(ext="pdf", within_days=1)
    assert r["ok"], r
    assert [m["name"] for m in r["matches"]] == ["fresh.pdf"]


def test_search_no_match_clean(tmp_workspace):
    (tmp_workspace / "a.txt").write_text("x")
    r = _files.search_files(query="unicorn")
    assert r["ok"] and r["matches"] == []
    assert "No files" in r["message"]


def test_search_traversal_stays_caged(tmp_path, tmp_workspace):
    # a juicy file OUTSIDE the workspace must be unreachable by any query
    (tmp_path / "secret.txt").write_text("outside")
    (tmp_workspace / "inside.txt").write_text("inside")
    for q in ("../secret", "..\secret", "secret", "../", "C:\\"):
        r = _files.search_files(query=q)
        assert r["ok"]
        assert all("secret" not in m["name"] for m in r["matches"]), (q, r)


def test_search_empty_query_fails(tmp_workspace):
    r = _files.search_files()
    assert not r["ok"]
    assert "name" in r["message"].lower() or "search" in r["message"].lower()
