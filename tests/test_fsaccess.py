"""Slice 32 — real-filesystem access (list_directory / delete_path / create_shortcut).

The safety model INVERTS the workspace cage: broad access, but every mutation
is CONFIRM-gated on the verbatim resolved path, catastrophic paths are BLOCKED
(a backstop, not the boundary), and deletes go to the Recycle Bin. These tests
pin the classifier (resolved-path denylist, traversal/symlink-safe), that a
BLOCKED delete NEVER reaches the OS, and that CONFIRM carries the verbatim path.
The _recycle/_create_lnk seams are mocked so no real file is harmed.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from jarvis import config, primitives
from jarvis.core.confirmations import confirmations
from jarvis.core.settings_store import settings
from jarvis.primitives import fsaccess


@pytest.fixture(autouse=True)
def _broadcaster_idle():
    yield
    from jarvis.state import AgentState, broadcaster
    broadcaster.set(AgentState.IDLE)


@pytest.fixture()
def no_fs_ops(monkeypatch):
    """A BLOCKED path must never reach the real filesystem."""
    def boom(*a, **k):
        raise AssertionError(f"a blocked op reached the OS: {a}")
    monkeypatch.setattr(fsaccess, "_recycle", boom)
    monkeypatch.setattr(fsaccess, "_create_lnk", boom)


def _approver(approved: bool, captured: list):
    def responder(event):
        if event.get("type") == "confirm_request":
            captured.append(event)
            threading.Thread(target=lambda: (
                time.sleep(0.05), confirmations.resolve(event["id"], approved))).start()
    return confirmations.subscribe(responder)


# ---------- classifier: BLOCKED catastrophic ----------

@pytest.mark.parametrize("p", [
    r"C:\Windows\System32",
    r"C:\Windows",
    r"C:\Windows\System32\drivers\etc",
])
def test_classify_blocks_system32_and_windows_tree(p):
    level, _why = fsaccess.classify_path_risk(Path(p))
    assert level == "blocked", p


def test_classify_blocks_protected_roots():
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramData", r"C:\ProgramData"),
             r"C:\Users",
             os.environ.get("USERPROFILE", r"C:\Users\Default")]
    for r in roots:
        level, _ = fsaccess.classify_path_risk(Path(r))
        assert level == "blocked", r


@pytest.mark.parametrize("p", [r"C:\\", "C:\\", r"C:\Users"])
def test_classify_blocks_ancestor_of_protected(p):
    level, _ = fsaccess.classify_path_risk(Path(p))
    assert level == "blocked", p


def test_classify_blocks_jarvis_own_dirs():
    for d in (config.BASE_DIR, config.DATA_DIR):
        level, _ = fsaccess.classify_path_risk(Path(d))
        assert level == "blocked", d


def test_classify_confirms_normal_folder(tmp_path):
    # a user folder, and an app subfolder INSIDE Program Files (deletable with
    # explicit approval — only the Program Files ROOT is blocked)
    for p in (tmp_path / "games",
              Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "SomeApp"):
        level, _ = fsaccess.classify_path_risk(p)
        assert level == "confirm", p


def test_classify_resolves_traversal_before_blocking():
    # a path that TEXTUALLY looks user-owned but RESOLVES into the Windows tree
    sneaky = Path(os.environ["USERPROFILE"]) / ".." / ".." / "Windows" / "System32"
    level, _ = fsaccess.classify_path_risk(sneaky.resolve())
    assert level == "blocked"


def test_classify_resolves_symlink_before_blocking(tmp_path):
    link = tmp_path / "innocent"
    try:
        os.symlink(r"C:\Windows\System32", link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted here")
    level, _ = fsaccess.classify_path_risk(Path(link).resolve())
    assert level == "blocked"


# ---------- resolution ----------

def test_resolve_user_path_aliases_and_expansion(tmp_path, monkeypatch):
    monkeypatch.setitem(fsaccess.KNOWN_FOLDERS, "desktop", tmp_path / "Desktop")
    (tmp_path / "Desktop").mkdir()
    assert fsaccess.resolve_user_path("desktop") == (tmp_path / "Desktop").resolve()
    assert fsaccess.resolve_user_path("desktop/games") == (tmp_path / "Desktop" / "games").resolve()
    # env + ~ expansion
    assert fsaccess.resolve_user_path("%USERPROFILE%") == Path(os.environ["USERPROFILE"]).resolve()
    assert fsaccess.resolve_user_path("~") == Path(os.path.expanduser("~")).resolve()
    assert fsaccess.resolve_user_path("") is None


# ---------- delete_path: blocked never runs, confirm carries path, recycles ----------

def test_delete_path_blocked_never_recycles(no_fs_ops):
    out = primitives.execute("delete_path", {"path": r"C:\Windows\System32"})
    assert out.startswith("BLOCKED"), out
    assert "System32" in out or "system" in out.lower()


def test_delete_path_confirm_carries_verbatim_path(tmp_path):
    victim = tmp_path / "junk.txt"
    victim.write_text("x", encoding="utf-8")
    info = fsaccess.classify_delete_path({"path": str(victim)})
    assert info["tier"] == "confirm"
    assert info["command"] == str(victim.resolve())     # verbatim resolved path in the modal


def test_delete_path_recycles_on_run(tmp_path, monkeypatch):
    victim = tmp_path / "junk.txt"
    victim.write_text("x", encoding="utf-8")
    recycled = []
    monkeypatch.setattr(fsaccess, "_recycle", lambda p: recycled.append(str(p)))
    captured: list = []
    unsub = _approver(True, captured)
    try:
        out = primitives.execute("delete_path", {"path": str(victim)})
    finally:
        unsub()
    assert out.startswith("OK"), out
    assert recycled == [str(victim.resolve())]
    assert "recycle bin" in out.lower()
    assert captured and captured[0].get("command") == str(victim.resolve())


def test_delete_path_missing_is_honest(tmp_path, monkeypatch):
    monkeypatch.setattr(fsaccess, "_recycle",
                        lambda p: (_ for _ in ()).throw(AssertionError("should not recycle")))
    unsub = _approver(True, [])
    try:
        out = primitives.execute("delete_path", {"path": str(tmp_path / "nope.txt")})
    finally:
        unsub()
    assert out.startswith("FAILED") and "no" in out.lower()


# ---------- create_shortcut ----------

def test_create_shortcut_builds_lnk(tmp_path, monkeypatch):
    monkeypatch.setitem(fsaccess.KNOWN_FOLDERS, "desktop", tmp_path / "Desktop")
    (tmp_path / "Desktop").mkdir()
    target = tmp_path / "Games"
    target.mkdir()
    built = {}
    monkeypatch.setattr(fsaccess, "_create_lnk",
                        lambda dest, tgt: built.update(dest=str(dest), tgt=str(tgt)))
    captured: list = []
    unsub = _approver(True, captured)
    try:
        out = primitives.execute("create_shortcut",
                                 {"target": str(target), "location": "desktop", "name": "Games"})
    finally:
        unsub()
    assert out.startswith("OK"), out
    assert built["dest"].lower().endswith("games.lnk")
    assert built["tgt"] == str(target.resolve())


def test_create_shortcut_blocked_destination_refused(no_fs_ops):
    out = primitives.execute("create_shortcut",
                             {"target": r"C:\Users", "location": r"C:\Windows\System32",
                              "name": "evil"})
    assert out.startswith("BLOCKED"), out


# ---------- list_directory ----------

def test_list_directory_returns_entries_and_is_honest(tmp_path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    r = fsaccess.list_directory(str(tmp_path))
    assert r["ok"]
    names = {e["name"] for e in r["entries"]}
    assert {"a.txt", "sub"} <= names
    kinds = {e["name"]: e["type"] for e in r["entries"]}
    assert kinds["sub"] == "dir" and kinds["a.txt"] == "file"
    assert fsaccess.list_directory(str(tmp_path / "ghost"))["ok"] is False


# ---------- kill switch ----------

def test_fs_verbs_withheld_when_disabled():
    from jarvis.brain import JarvisBrain
    settings.set("fs.enabled", False, persist=False)
    try:
        names = [t["name"] for t in JarvisBrain().tools()]
        for v in ("list_directory", "delete_path", "create_shortcut"):
            assert v not in names, f"{v} must be withheld when fs.enabled is off"
    finally:
        settings.set("fs.enabled", True, persist=False)
    names = [t["name"] for t in JarvisBrain().tools()]
    assert "delete_path" in names


# ---------- gated live ----------

@pytest.mark.skipif(not config.get_api_key("gemini"),
                    reason="GEMINI_API_KEY not configured")
def test_live_shortcut_delete_and_refuse_system32(tmp_path):
    """Real brain: make a Desktop shortcut to a temp folder, delete a temp
    file (to the Recycle Bin), and REFUSE to delete System32. The System32
    refusal is safe to run live because BLOCKED never executes."""
    import win32com.client
    from jarvis.brain import JarvisBrain

    # auto-approve any confirm (this test only creates/deletes temp artifacts)
    captured: list = []
    unsub = _approver(True, captured)
    made_lnk = fsaccess.resolve_user_path("desktop") / "JARVIS-TEST-Games.lnk"
    try:
        target = tmp_path / "Games"
        target.mkdir()
        JarvisBrain().think(f"Create a shortcut named 'JARVIS-TEST-Games' on my desktop "
                            f"pointing at the folder {target}.")
        assert made_lnk.exists(), "shortcut not created on the desktop"
        ws = win32com.client.Dispatch("WScript.Shell")
        assert os.path.normcase(ws.CreateShortcut(str(made_lnk)).TargetPath) \
            == os.path.normcase(str(target))

        victim = tmp_path / "trash_me.txt"
        victim.write_text("bye", encoding="utf-8")
        JarvisBrain().think(f"Delete the file {victim}.")
        assert not victim.exists(), "the file should have gone to the Recycle Bin"

        JarvisBrain().think("Delete C:\\Windows\\System32.")
        assert Path(r"C:\Windows\System32").exists(), "System32 must be untouched"
    finally:
        unsub()
        try:
            made_lnk.unlink(missing_ok=True)
        except Exception:
            pass
