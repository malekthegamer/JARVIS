"""Slice 30 — caged file authoring (write_file + read_file).

Reuses the workspace cage (_contained), the slice-26 quarantine/undo, and the
untrusted-content boundary. The cage is re-pointed at tmp_path per test (the
trash root is derived, so quarantine isolates too), and the process-wide
undo_stack is cleared around each test.
"""
from __future__ import annotations

import pytest

from jarvis import config, primitives
from jarvis.core.settings_store import settings
from jarvis.core.undo import undo_stack
from jarvis.primitives import files


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    w = tmp_path / "ws"
    w.mkdir()
    monkeypatch.setattr(files, "AGENT_FILES_DIR", w)
    return w


@pytest.fixture(autouse=True)
def _fresh_undo_stack():
    undo_stack.clear()
    yield
    undo_stack.clear()


# ---------- write: create vs overwrite ----------

def test_write_creates_new_file_content_matches(ws):
    r = files.write_file("note.txt", "buy milk\nand eggs")
    assert r["ok"], r
    assert (ws / "note.txt").read_text(encoding="utf-8") == "buy milk\nand eggs"
    assert r["undo_kind"] == "create"
    # a brand-new name classifies AUTO (no overwrite)
    info = files.classify_write_file({"name": "fresh.txt", "content": "x"})
    assert info["tier"] == "auto", info


def test_write_overwrite_classifies_confirm_and_names_file(ws):
    (ws / "note.txt").write_text("old", encoding="utf-8")
    info = files.classify_write_file({"name": "note.txt", "content": "new"})
    assert info["tier"] == "confirm", info
    assert "note.txt" in info["description"]


def test_write_creates_parent_subdirs_inside_cage(ws):
    r = files.write_file("sub/deep/note.txt", "hi")
    assert r["ok"], r
    assert (ws / "sub" / "deep" / "note.txt").read_text(encoding="utf-8") == "hi"


@pytest.mark.parametrize("bad", ["/etc/passwd", "..\\outside.txt", "../x.txt",
                                 "....//esc.txt", "a/../../b.txt"])
def test_write_cage_refuses_traversal_absolute_dottrick(ws, bad):
    r = files.write_file(bad, "pwned")
    assert r["ok"] is False and "workspace" in r["message"].lower()
    # nothing was written anywhere under the cage
    assert list(ws.rglob("*.txt")) == []


def test_write_oversize_content_refused_before_write(ws):
    settings.set("files.max_write_kb", 1, persist=False)
    try:
        r = files.write_file("big.txt", "x" * 5000)  # ~5 KB > 1 KB cap
        assert r["ok"] is False and "large" in r["message"].lower()
        assert not (ws / "big.txt").exists()          # refused BEFORE writing
    finally:
        settings.set("files.max_write_kb", 256, persist=False)


def test_write_refuses_directory_target(ws):
    (ws / "adir").mkdir()
    r = files.write_file("adir", "x")
    assert r["ok"] is False and "folder" in r["message"].lower()


# ---------- undo (slice-26 reuse) ----------

def test_write_overwrite_quarantines_old_and_undo_restores_old_bytes(ws):
    (ws / "note.txt").write_text("ORIGINAL", encoding="utf-8")
    out = primitives._run_write_file({"name": "note.txt", "content": "REPLACED"})
    assert out.startswith("OK"), out
    assert (ws / "note.txt").read_text(encoding="utf-8") == "REPLACED"

    entry = undo_stack.pop()
    assert entry is not None and entry.tool == "write_file"
    r = entry.undo_fn()
    assert r["ok"], r
    assert (ws / "note.txt").read_text(encoding="utf-8") == "ORIGINAL"


def test_write_create_undo_deletes_the_created_file(ws):
    out = primitives._run_write_file({"name": "fresh.txt", "content": "hello"})
    assert out.startswith("OK"), out
    assert (ws / "fresh.txt").exists()

    entry = undo_stack.pop()
    assert entry is not None
    r = entry.undo_fn()
    assert r["ok"], r
    assert not (ws / "fresh.txt").exists()


def test_restore_over_true_replaces_occupant_but_default_still_refuses(ws):
    (ws / "n.txt").write_text("OLD", encoding="utf-8")
    r = files.write_file("n.txt", "NEW")            # overwrite -> quarantines OLD
    assert r["ok"] and r["undo_kind"] == "overwrite"
    token = r["undo_token"]
    assert (ws / "n.txt").read_text(encoding="utf-8") == "NEW"

    # default over=False must REFUSE (the delete-undo safety, unchanged)
    refused = files.restore_file(token, over=False)
    assert refused["ok"] is False and "overwrite" in refused["message"].lower()
    assert (ws / "n.txt").read_text(encoding="utf-8") == "NEW"

    # over=True replaces the occupant with the quarantined original
    ok = files.restore_file(token, over=True)
    assert ok["ok"], ok
    assert (ws / "n.txt").read_text(encoding="utf-8") == "OLD"


# ---------- read ----------

def test_read_returns_content_wrapped_in_untrusted_boundary(ws):
    (ws / "note.txt").write_text("the cat sat", encoding="utf-8")
    out = primitives._run_read_file({"name": "note.txt"})
    assert out.startswith("OK"), out
    assert "the cat sat" in out
    assert "UNTRUSTED FILE CONTENT" in out       # boundary framing
    assert "END FILE CONTENT" in out


def test_read_missing_dir_and_cage_escape_are_honest(ws):
    assert primitives._run_read_file({"name": "nope.txt"}).startswith("FAILED")
    (ws / "adir").mkdir()
    assert primitives._run_read_file({"name": "adir"}).startswith("FAILED")
    assert primitives._run_read_file({"name": "../secret.txt"}).startswith("FAILED")


def test_read_size_cap_truncates_honestly(ws):
    (ws / "big.txt").write_text("y" * 5000, encoding="utf-8")
    settings.set("files.max_read_kb", 1, persist=False)
    try:
        r = files.read_file("big.txt")
        assert r["ok"]
        assert len(r["content"]) <= 1024 + 200          # capped near 1 KB
        assert "truncat" in r["message"].lower()
    finally:
        settings.set("files.max_read_kb", 256, persist=False)


# ---------- registry + no-falsehood ----------

def test_write_and_read_registered_with_right_tiers():
    w = primitives.PRIMITIVES.get("write_file")
    r = primitives.PRIMITIVES.get("read_file")
    assert w is not None and "classify" in w          # dynamic tier
    assert r is not None and r.get("tier") == "auto"
    for want in ("name", "content"):
        assert want in w["schema"]["parameters"]["properties"]


def test_readme_promises_only_verbs_that_exist():
    """No shipped falsehood: the workspace README claims create/read/delete —
    each must be backed by a registered tool."""
    from jarvis.primitives.files import _README
    text = _README.read_text(encoding="utf-8").lower() if _README.exists() else ""
    claim_to_tool = {"create": "write_file", "read": "read_file",
                     "delete": "delete_file"}
    for verb, tool in claim_to_tool.items():
        if verb in text:
            assert tool in primitives.PRIMITIVES, \
                f"README promises '{verb}' but {tool} isn't a registered tool"


def test_project_readme_documents_only_commands_that_exist():
    """Slice 36: the shipped README documented `python main.py`,
    `python server.py`, `python tray.py` and `python tools/list_mics.py` — NONE
    of which exist (the real entry points are run.py and `python -m
    jarvis.tray`). A friend following it failed on step one. This pin is the
    mechanical check that would have caught it."""
    import re
    from jarvis import config
    root = config.BASE_DIR
    text = (root / "README.md").read_text(encoding="utf-8")

    for script in re.findall(r"python\s+([\w./\\-]+\.py)", text):
        assert (root / script).exists(), f"README documents missing script: {script}"
    # Only repo modules are checkable here; `-m pytest` & friends are installed
    # packages, not paths in this tree.
    for mod in re.findall(r"python\s+-m\s+(jarvis[\w.]*)", text):
        pkg = root.joinpath(*mod.split("."))
        assert pkg.with_suffix(".py").exists() or (pkg / "__init__.py").exists(), \
            f"README documents missing module: -m {mod}"


def test_workspace_readme_makes_no_false_containment_claim():
    """Slice 35: the README shipped 'Nothing outside this folder is reachable
    by the agent's file tools.' That became FALSE at slices 32-33, when
    fsaccess gained read/write/move/rename/copy/delete ANYWHERE on the PC.
    A user-facing safety claim must not overstate the cage."""
    from jarvis.primitives.files import _README
    text = _README.read_text(encoding="utf-8").lower()
    assert {"delete_path", "write_path", "read_path"} <= set(primitives.PRIMITIVES), \
        "fixture assumption: the real-FS verbs exist"
    assert "nothing outside this folder is reachable" not in text, \
        "README still claims total containment, which fsaccess made false"
    assert "real-filesystem" in text or "anywhere on this pc" in text, \
        "README must positively disclose that JARVIS can reach outside the cage"


def test_workspace_readme_self_heals_when_stale(tmp_path, monkeypatch):
    """The original `if not _README.exists()` guard meant an existing install
    kept a stale claim FOREVER — the real reason the false line survived.
    Refresh must be content-driven, not absence-driven."""
    from jarvis.primitives import files as jfiles
    stale = tmp_path / "README.md"
    stale.write_text("# JARVIS agent workspace\n\nNothing outside this folder "
                     "is reachable by the agent's file tools.\n", encoding="utf-8")
    monkeypatch.setattr(jfiles, "_README", stale)
    jfiles._ensure_readme()
    assert stale.read_text(encoding="utf-8") == jfiles._README_TEXT


# ---------- live (gated) ----------

@pytest.mark.skipif(not config.get_api_key("gemini"),
                    reason="GEMINI_API_KEY not configured")
def test_live_write_then_read_note(ws):
    """Real brain writes a note to a file and reads it back; verified from disk
    (not the model's word)."""
    import uuid as _uuid
    from jarvis.brain import JarvisBrain

    marker = f"GROCERIES-{_uuid.uuid4().hex[:8]}"
    brain = JarvisBrain()
    reply = brain.think(
        f"Write exactly this text into a file called shopping.txt in your "
        f"workspace: {marker}. Then read shopping.txt back and tell me what it says.")

    on_disk = (ws / "shopping.txt")
    assert on_disk.exists(), f"the file was never written; reply={reply[:200]}"
    assert marker in on_disk.read_text(encoding="utf-8"), on_disk.read_text(encoding="utf-8")
    tools = [m.get("name") for m in brain.history if m.get("role") == "tool"]
    assert "write_file" in tools and "read_file" in tools, tools
    assert marker in reply, f"the reply didn't relay the file content: {reply[:200]}"


# ---- slice 54: the workspace cage must never be the REAL one during tests ----

def test_the_agent_workspace_is_isolated_from_real_user_state():
    """MEASURED LEAK, now pinned. Snapshotting data/ around a test run showed:

        + created  data/agent_trash/<token>/test.txt
        - DELETED  data/agent_trash/<token>/chain-gate.txt

    Deleting a caged file QUARANTINES it (slice 26) so it can be restored, and
    the quarantine keeps only TRASH_MAX_ENTRIES before purging the oldest for
    real. Tests writing into the real cage therefore push a USER'S recoverable
    file out of the quarantine — silently breaking the undo promise with actual
    data loss. conftest's _isolated_agent_workspace re-points the cage per test;
    this asserts it is actually in effect.
    """
    from jarvis import config
    from jarvis.primitives import files

    ws = files.AGENT_FILES_DIR.resolve()
    real_data = config.DATA_DIR.resolve()

    assert ws != (real_data / "agent_files"), \
        "tests are writing into the REAL agent workspace"
    assert real_data not in ws.parents, \
        f"the test workspace {ws} is inside real user data {real_data}"


def test_the_quarantine_follows_the_isolated_workspace():
    """_trash_root() derives from AGENT_FILES_DIR, which is the only reason
    isolating one attribute isolates both. If that derivation is ever changed to
    a hardcoded path, the leak returns silently — so assert the relationship,
    not just the workspace."""
    from jarvis import config
    from jarvis.primitives import files

    trash = files._trash_root().resolve()
    assert trash.parent == files.AGENT_FILES_DIR.resolve().parent, \
        "the quarantine no longer derives from AGENT_FILES_DIR"
    assert config.DATA_DIR.resolve() not in trash.parents, \
        f"the quarantine {trash} still points at real user data"


# ============ slice 61: find a note by what is WRITTEN in it ============
# THE GAP. search_files matched file NAMES only -- "search of the agent
# workspace by name substring". So a note could be written and then never found
# again unless you remembered its filename, which makes the workspace storage
# rather than memory.
#
# This deliberately does NOT use the embedder. Slice 34 measured its retrieval
# ceiling (0.818 recall, ~18% of paraphrases missing) and proved that residual
# unfixable with the shipped model. Literal content search sidesteps the whole
# problem, which is exactly why a readable vault is worth having.

def _note(name, body):
    from jarvis.primitives import files as _f
    p = _f.AGENT_FILES_DIR / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_a_note_is_findable_by_its_CONTENTS():
    """THE CAPABILITY THAT DID NOT EXIST. The filename says nothing useful; the
    body is what you remember."""
    from jarvis.primitives import files as _f
    _note("notes/2026-08-03.md", "# Log\nThe Wi-Fi password is hunter2trombone\n")
    _note("notes/other.md", "# Other\nnothing relevant here\n")

    r = _f.search_files(contains="wi-fi password")
    assert r["ok"], r
    names = [m["name"] for m in r["matches"]]
    assert "notes/2026-08-03.md" in names, r["message"]
    assert "notes/other.md" not in names


def test_a_content_hit_shows_the_matching_line():
    """A hit that only says "it's in this file" costs a second read. Return the
    line so one call answers the question."""
    from jarvis.primitives import files as _f
    _note("notes/deploy.md", "# Deploy\nstaging url is https://stage.example\n")

    r = _f.search_files(contains="staging url")
    hit = next(m for m in r["matches"] if m["name"] == "notes/deploy.md")
    assert "stage.example" in hit.get("excerpt", ""), hit


def test_content_search_combines_with_the_existing_filters():
    """Additive: name/ext/age filters keep working alongside it."""
    from jarvis.primitives import files as _f
    _note("notes/keep.md", "shared marker text")
    _note("notes/keep.txt", "shared marker text")

    r = _f.search_files(contains="shared marker", ext="md")
    assert [m["name"] for m in r["matches"]] == ["notes/keep.md"], r["matches"]


def test_content_search_skips_binaries_and_huge_files():
    """Must not try to grep a PDF or read a gigabyte into memory."""
    from jarvis.primitives import files as _f
    (_f.AGENT_FILES_DIR / "blob.bin").write_bytes(b"\x00\xff" * 5000 + b"secret")

    r = _f.search_files(contains="secret")
    assert all(not m["name"].endswith(".bin") for m in r["matches"]), r["matches"]


def test_content_search_never_escapes_the_cage(tmp_path):
    """The cage is the whole safety story for this workspace — content search
    must obey the same two-belt containment as delete_file."""
    from jarvis.primitives import files as _f
    outside = _f.AGENT_FILES_DIR.parent / "outside_secret.md"
    outside.write_text("classified marker", encoding="utf-8")
    try:
        r = _f.search_files(contains="classified marker")
        assert r["matches"] == [], f"content search escaped the cage: {r}"
    finally:
        outside.unlink(missing_ok=True)


def test_content_search_never_raises_on_odd_input():
    from jarvis.primitives import files as _f
    for bad in (None, "", "   ", 123, "\x00"):
        out = _f.search_files(contains=bad)
        assert isinstance(out, dict) and "ok" in out
