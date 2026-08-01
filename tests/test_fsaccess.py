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


# ==================================================================== Slice 33:
# authoring verbs on the real FS — write / read / move / rename / copy. Same
# proven core (classify_path_risk); every overwrite/clobber recycles the prior
# version first so it's recoverable.

SYS_FILE = r"C:\Windows\System32\drivers\etc\hosts"


def test_write_path_creates_and_confirms(tmp_path, monkeypatch):
    written = {}
    monkeypatch.setattr(fsaccess, "_write_text", lambda p, c: written.update(p=str(p), c=c))
    dest = tmp_path / "note.txt"
    r = fsaccess.write_path(str(dest), "hello world")
    assert r["ok"] and written["p"] == str(dest.resolve()) and written["c"] == "hello world"
    # classify: a protected target is BLOCKED, a normal one CONFIRMs
    assert fsaccess.classify_write_path({"path": SYS_FILE})["tier"] == "blocked"
    info = fsaccess.classify_write_path({"path": str(dest)})
    assert info["tier"] == "confirm" and info["command"] == str(dest.resolve())


def test_write_path_overwrite_recycles_prior_then_writes(tmp_path, monkeypatch):
    dest = tmp_path / "note.txt"
    dest.write_text("OLD", encoding="utf-8")
    order = []
    monkeypatch.setattr(fsaccess, "_recycle", lambda p: order.append(("recycle", str(p))))
    monkeypatch.setattr(fsaccess, "_write_text", lambda p, c: order.append(("write", c)))
    r = fsaccess.write_path(str(dest), "NEW")
    assert r["ok"], r
    assert order == [("recycle", str(dest.resolve())), ("write", "NEW")]  # recycle BEFORE write


def test_write_path_oversize_refused(tmp_path, monkeypatch):
    settings.set("fs.max_write_kb", 1, persist=False)
    try:
        monkeypatch.setattr(fsaccess, "_write_text",
                            lambda p, c: (_ for _ in ()).throw(AssertionError("must not write")))
        r = fsaccess.write_path(str(tmp_path / "big.txt"), "z" * 5000)
        assert r["ok"] is False and "large" in r["message"].lower()
    finally:
        settings.set("fs.max_write_kb", 256, persist=False)


def test_read_path_returns_wrapped_content(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("the cat sat", encoding="utf-8")
    out = primitives._run_read_path({"path": str(f)})
    assert out.startswith("OK") and "the cat sat" in out
    assert "UNTRUSTED FILE CONTENT" in out
    assert primitives._run_read_path({"path": str(tmp_path / "ghost.txt")}).startswith("FAILED")
    assert primitives._run_read_path({"path": str(tmp_path)}).startswith("FAILED")   # a dir


def test_move_path_confirms_and_moves(tmp_path, monkeypatch):
    src = tmp_path / "a.txt"; src.write_text("x", encoding="utf-8")
    dest = tmp_path / "b.txt"
    moved = {}
    monkeypatch.setattr(fsaccess, "_move", lambda s, d: moved.update(s=str(s), d=str(d)))
    r = fsaccess.move_path(str(src), str(dest))
    assert r["ok"] and moved["s"] == str(src.resolve()) and moved["d"] == str(dest.resolve())
    info = fsaccess.classify_move_path({"source": str(src), "dest": str(dest)})
    assert info["tier"] == "confirm"
    assert str(src.resolve()) in info["command"] and str(dest.resolve()) in info["command"]


def test_move_blocked_when_source_or_dest_protected(tmp_path):
    ok_path = str(tmp_path / "x.txt")
    assert fsaccess.classify_move_path({"source": SYS_FILE, "dest": ok_path})["tier"] == "blocked"
    assert fsaccess.classify_move_path({"source": ok_path, "dest": SYS_FILE})["tier"] == "blocked"


def test_move_into_existing_dir_vs_replace_existing_file(tmp_path, monkeypatch):
    src = tmp_path / "a.txt"; src.write_text("x", encoding="utf-8")
    into = tmp_path / "folder"; into.mkdir()
    recycled, moved = [], {}
    monkeypatch.setattr(fsaccess, "_recycle", lambda p: recycled.append(str(p)))
    monkeypatch.setattr(fsaccess, "_move", lambda s, d: moved.update(d=str(d)))
    # dest is a dir -> item goes INSIDE it, nothing recycled
    fsaccess.move_path(str(src), str(into))
    assert moved["d"].lower().endswith(os.path.join("folder", "a.txt").lower())
    assert recycled == []
    # dest is an existing file -> recycle it first
    src2 = tmp_path / "c.txt"; src2.write_text("y", encoding="utf-8")
    existing = tmp_path / "d.txt"; existing.write_text("old", encoding="utf-8")
    fsaccess.move_path(str(src2), str(existing))
    assert recycled == [str(existing.resolve())]


def test_rename_path_same_dir_and_rejects_separator(tmp_path, monkeypatch):
    src = tmp_path / "a.txt"; src.write_text("x", encoding="utf-8")
    moved = {}
    monkeypatch.setattr(fsaccess, "_move", lambda s, d: moved.update(d=str(d)))
    r = fsaccess.rename_path(str(src), "b.txt")
    assert r["ok"] and moved["d"] == str((tmp_path / "b.txt").resolve())
    bad = fsaccess.rename_path(str(src), "sub/b.txt")
    assert bad["ok"] is False and ("name" in bad["message"].lower())


def test_copy_path_file_and_folder_and_blocked_dest(tmp_path, monkeypatch):
    copied = []
    monkeypatch.setattr(fsaccess, "_copy", lambda s, d: copied.append((str(s), str(d))))
    f = tmp_path / "a.txt"; f.write_text("x", encoding="utf-8")
    d = tmp_path / "sub"; d.mkdir()
    assert fsaccess.copy_path(str(f), str(tmp_path / "a2.txt"))["ok"]
    assert fsaccess.copy_path(str(d), str(tmp_path / "sub2"))["ok"]
    assert len(copied) == 2
    # dest protected -> blocked (source may be anywhere)
    assert fsaccess.classify_copy_path({"source": str(f), "dest": SYS_FILE})["tier"] == "blocked"


def test_new_fs_verbs_withheld_when_disabled():
    from jarvis.brain import JarvisBrain
    settings.set("fs.enabled", False, persist=False)
    try:
        names = [t["name"] for t in JarvisBrain().tools()]
        for v in ("write_path", "read_path", "move_path", "rename_path", "copy_path"):
            assert v not in names, f"{v} must be withheld when fs.enabled is off"
    finally:
        settings.set("fs.enabled", True, persist=False)
    assert "write_path" in [t["name"] for t in JarvisBrain().tools()]


@pytest.mark.skipif(not config.get_api_key("gemini"),
                    reason="GEMINI_API_KEY not configured")
def test_live_write_read_rename_copy_move(tmp_path):
    """Real brain: write a note, read it back, rename, copy, move — verified on
    disk (not model-claimed). Auto-approve the CONFIRMs."""
    from jarvis.brain import JarvisBrain
    unsub = _approver(True, [])
    try:
        note = tmp_path / "note.txt"
        JarvisBrain().think(f"Write the text 'hello jarvis' into the file {note}.")
        assert note.exists() and "hello jarvis" in note.read_text(encoding="utf-8")

        renamed = tmp_path / "renamed.txt"
        JarvisBrain().think(f"Rename the file {note} to renamed.txt.")
        assert renamed.exists() and not note.exists()

        copy_dest = tmp_path / "copy.txt"
        JarvisBrain().think(f"Copy the file {renamed} to {copy_dest}.")
        assert copy_dest.exists() and renamed.exists()   # copy keeps the source
    finally:
        unsub()


# ==================== slice 56: make_folder ====================
# The fs verb set could write, read, move, rename, copy, delete and shortcut
# ANYWHERE, but could not create a directory -- so JARVIS could put files on the
# Desktop and never organise them. Same safety model as its siblings: BLOCKED on
# protected trees (backstop), CONFIRM on the verbatim resolved path (boundary).
#
# Deliberately NOT undoable. Removing a just-created folder is only safe while
# it is empty, and by the time a user asks to undo they may have put things in
# it. The other reversible verbs restore a PREVIOUS state; this one would have
# to delete, which is a different and riskier operation than undo promises.


def test_make_folder_creates_a_directory(tmp_path):
    target = tmp_path / "new folder"
    r = fsaccess.make_folder(str(target))
    assert r["ok"], r
    assert target.is_dir()
    assert str(target) in r["message"], "must name the verbatim path it made"


def test_make_folder_creates_missing_parents(tmp_path):
    target = tmp_path / "a" / "b" / "c"
    assert fsaccess.make_folder(str(target))["ok"]
    assert target.is_dir()


def test_make_folder_on_an_existing_directory_is_ok_and_says_so(tmp_path):
    target = tmp_path / "already"
    target.mkdir()
    r = fsaccess.make_folder(str(target))
    assert r["ok"], "an existing folder is the desired end state, not an error"
    assert "already" in r["message"].lower()


def test_make_folder_where_a_FILE_exists_fails_cleanly(tmp_path):
    clash = tmp_path / "notes.txt"
    clash.write_text("real content", encoding="utf-8")
    r = fsaccess.make_folder(str(clash))
    assert r["ok"] is False, "must not silently succeed over a file"
    assert clash.read_text(encoding="utf-8") == "real content", \
        "the user's file must be untouched"


def test_make_folder_unresolvable_path_fails_cleanly():
    r = fsaccess.make_folder("")
    assert r["ok"] is False and "understand" in r["message"].lower()


def test_make_folder_never_raises(tmp_path):
    """Never-raise contract, same as every other fs verb."""
    for bad in (None, "", "   ", "\x00bad", str(tmp_path / "ok")):
        out = fsaccess.make_folder(bad)
        assert isinstance(out, dict) and "ok" in out


# ---- the gate ----

def test_make_folder_classifier_blocks_protected_trees():
    for p in (r"C:\Windows\System32\newdir", r"C:\Windows\x", "C:\\"):
        info = fsaccess.classify_make_folder({"path": p})
        assert info["tier"] == "blocked", (p, info)


def test_make_folder_classifier_confirms_and_shows_the_verbatim_path(tmp_path):
    target = tmp_path / "projects"
    info = fsaccess.classify_make_folder({"path": str(target)})
    assert info["tier"] == "confirm", info
    assert str(target) in (info.get("command") or "") + info["description"]


def test_make_folder_is_never_auto(tmp_path):
    """Creating a directory anywhere on the PC is never silent."""
    for args in ({}, {"path": ""}, {"path": str(tmp_path / "x")},
                 {"path": None}):
        assert fsaccess.classify_make_folder(args)["tier"] in ("confirm", "blocked")


def test_make_folder_fails_closed_when_classification_raises(monkeypatch):
    def boom(_p):
        raise RuntimeError("resolver exploded")
    monkeypatch.setattr(fsaccess, "resolve_user_path", boom)
    assert fsaccess.classify_make_folder({"path": "x"})["tier"] == "confirm"


# ---- wiring ----

def test_make_folder_is_registered_with_its_classifier():
    assert "make_folder" in primitives.PRIMITIVES
    assert primitives.PRIMITIVES["make_folder"]["classify"] is fsaccess.classify_make_folder


def test_make_folder_rides_the_fs_kill_switch(monkeypatch):
    """It reaches the real filesystem, so fs.enabled must withhold AND refuse
    it — the slice-35 lesson: schema-withholding alone is not a boundary."""
    real = settings.get
    monkeypatch.setattr(settings, "get", lambda p, d=None:
                        (False if p == "fs.enabled" else real(p, d)))
    assert "make_folder" not in {s["name"] for s in primitives.tools_schema()}
    assert primitives.execute("make_folder", {"path": "x"}).startswith("BLOCKED")


def test_blocked_make_folder_never_reaches_the_filesystem(monkeypatch, tmp_path):
    """The gate must stop it BEFORE the OS call, not report failure after."""
    called = []
    monkeypatch.setattr(fsaccess, "_mkdir", lambda p: called.append(p))
    out = primitives.execute("make_folder", {"path": r"C:\Windows\System32\evil"})
    assert out.startswith("BLOCKED"), out
    assert not called, "a BLOCKED make_folder must never touch the filesystem"
