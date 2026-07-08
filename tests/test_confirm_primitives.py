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
