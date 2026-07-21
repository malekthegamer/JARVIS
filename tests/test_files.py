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
