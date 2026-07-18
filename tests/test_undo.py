"""Slice 26 — undo (spec §1.4's last unbuilt clause).

Deterministic tests for the undo stack and every capture point. The scope
boundary is itself pinned by a test: only volume/mute/brightness/DND,
remember, and delete_file are undoable — media keys, tab closes and emails
must NEVER land on the stack (they are categorically irreversible; a fake
"undo" there would be a lie).

Mocking mirrors test_system.py (module-attr monkeypatch; fake DND toggle via
the _dnd_session seam) and test_email.py (workspace cage re-pointed at
tmp_path). The undo stack is a process-wide singleton like memory_store, so
an autouse fixture clears it around every test here.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from jarvis import primitives
from jarvis.brain import JarvisBrain
from jarvis.core.memory import MemoryStore
from jarvis.core.undo import UndoEntry, undo_stack
from jarvis.primitives import files, system


@pytest.fixture(autouse=True)
def _fresh_undo_stack():
    """The stack is process-global; other test files' wrapper calls may have
    pushed entries. Clean slate in, clean slate out."""
    undo_stack.clear()
    yield
    undo_stack.clear()


# ---------- fakes ----------

class _FakeAudio:
    """Stand-in audio state behind get_volume/set_volume/set_mute."""
    def __init__(self, level=40, muted=False):
        self.level = level
        self.muted = muted

    def get(self):
        return {"ok": True, "level": self.level, "muted": self.muted,
                "message": f"Volume is {self.level}%."}

    def set_level(self, level):
        self.level = float(level)
        return {"ok": True, "message": f"Volume set to {level:g}%."}

    def set_muted(self, muted):
        self.muted = bool(muted)
        return {"ok": True, "message": "Muted." if muted else "Unmuted."}


class _FakeToggle:
    """Same shape as test_system.py's — the real UIA ToggleSwitch stand-in."""
    def __init__(self, state):
        self._state = state
        self.toggles = 0

    def state(self):
        return self._state

    def toggle(self):
        self.toggles += 1
        self._state = 1 - self._state


def _fake_session(toggle):
    @contextmanager
    def session():
        yield toggle
    return session


def _wire_audio(monkeypatch, audio: _FakeAudio):
    monkeypatch.setattr(system, "get_volume", audio.get)
    monkeypatch.setattr(system, "set_volume", audio.set_level)
    monkeypatch.setattr(system, "set_mute", audio.set_muted)


def _caged_workspace(tmp_path, monkeypatch):
    """Re-point the file cage at tmp (test_email.py pattern). The trash root
    is DERIVED from the workspace (parent / 'agent_trash'), so this one patch
    isolates quarantine too — no real data/ pollution."""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(files, "AGENT_FILES_DIR", ws)
    return ws


# ---------- 1-4: system settings restore ----------

def test_volume_undo_restores_previous_level(monkeypatch):
    audio = _FakeAudio(level=40)
    _wire_audio(monkeypatch, audio)

    out = primitives._run_set_volume({"level": 15})
    assert out.startswith("OK"), out
    assert audio.level == 15

    entry = undo_stack.pop()
    assert entry is not None and entry.tool == "set_volume"
    r = entry.undo_fn()
    assert r["ok"] is True
    assert audio.level == 40

    # A FAILED set must push nothing — there is no change to reverse.
    monkeypatch.setattr(system, "set_volume",
                        lambda level: {"ok": False, "message": "boom"})
    out = primitives._run_set_volume({"level": 99})
    assert out.startswith("FAILED")
    assert undo_stack.pop() is None


def test_mute_undo_restores_previous_state(monkeypatch):
    audio = _FakeAudio(muted=False)
    _wire_audio(monkeypatch, audio)

    out = primitives._run_set_mute({"muted": True})
    assert out.startswith("OK"), out
    assert audio.muted is True

    entry = undo_stack.pop()
    assert entry is not None and entry.tool == "set_mute"
    r = entry.undo_fn()
    assert r["ok"] is True
    assert audio.muted is False


def test_brightness_undo_restores_previous_level(monkeypatch):
    state = {"level": 80}
    monkeypatch.setattr(system, "get_brightness",
                        lambda: {"ok": True, "level": state["level"],
                                 "message": f"Brightness is {state['level']}%."})

    def fake_set(level):
        state["level"] = int(level)
        return {"ok": True, "message": f"Brightness set to {level:g}%."}
    monkeypatch.setattr(system, "set_brightness", fake_set)

    out = primitives._run_set_brightness({"level": 30})
    assert out.startswith("OK"), out
    assert state["level"] == 30

    entry = undo_stack.pop()
    assert entry is not None and entry.tool == "set_brightness"
    r = entry.undo_fn()
    assert r["ok"] is True
    assert state["level"] == 80

    # Unreadable pre-state (this monitor's honest reality) -> nothing to
    # restore to -> no push, even if the set were to succeed.
    monkeypatch.setattr(system, "get_brightness",
                        lambda: {"ok": False, "level": None, "message": "no DDC/CI"})
    primitives._run_set_brightness({"level": 50})
    assert undo_stack.pop() is None


def test_dnd_undo_restores_previous_toggle_state(monkeypatch):
    tgl = _FakeToggle(0)
    monkeypatch.setattr(system, "_dnd_session", _fake_session(tgl))

    out = primitives._run_set_dnd({"enabled": True})
    assert out.startswith("OK"), out
    assert tgl._state == 1

    entry = undo_stack.pop()
    assert entry is not None and entry.tool == "set_dnd"
    r = entry.undo_fn()
    assert r["ok"] is True
    assert tgl._state == 0

    # Already-in-desired-state is a no-op — nothing changed, nothing to undo.
    out = primitives._run_set_dnd({"enabled": False})
    assert out.startswith("OK")
    assert undo_stack.pop() is None


# ---------- 5: memory ----------

def test_remember_undo_removes_only_the_just_added_memory(tmp_path):
    brain = JarvisBrain()
    brain.memory = MemoryStore(tmp_path / "m.bin")
    brain.memory.add("an older fact that must survive")

    result = brain._memory_tool("remember", {"text": "the sky is teal today"})
    assert result.startswith("OK"), result
    assert len(brain.memory.all()) == 2

    out = brain._execute_tool("undo_last_action", {})
    assert out.startswith("OK"), out
    remaining = brain.memory.all()
    assert len(remaining) == 1
    assert remaining[0]["text"] == "an older fact that must survive"


# ---------- 6-8: file quarantine ----------

def test_delete_file_undo_restores_quarantined_file_to_original_path(
        tmp_path, monkeypatch):
    ws = _caged_workspace(tmp_path, monkeypatch)
    target = ws / "sub" / "note.txt"
    target.parent.mkdir()
    target.write_text("keep me", encoding="utf-8")

    out = primitives._run_delete_file({"name": "sub/note.txt"})
    assert out.startswith("OK"), out
    assert not target.exists()

    entry = undo_stack.pop()
    assert entry is not None and entry.tool == "delete_file"
    r = entry.undo_fn()
    assert r["ok"] is True, r
    assert target.read_text(encoding="utf-8") == "keep me"


def test_delete_file_undo_fails_honestly_when_original_path_now_occupied(
        tmp_path, monkeypatch):
    ws = _caged_workspace(tmp_path, monkeypatch)
    target = ws / "note.txt"
    target.write_text("original", encoding="utf-8")

    out = primitives._run_delete_file({"name": "note.txt"})
    assert out.startswith("OK"), out
    entry = undo_stack.pop()

    target.write_text("newer occupant", encoding="utf-8")
    r = entry.undo_fn()
    assert r["ok"] is False
    assert "overwrite" in r["message"].lower() or "exists" in r["message"].lower()
    # The occupant is untouched AND the quarantined original still exists.
    assert target.read_text(encoding="utf-8") == "newer occupant"
    trash = files._trash_root()
    assert any(p.is_file() for p in trash.rglob("*")), \
        "quarantined original must survive a refused restore"


def test_quarantine_retention_cap_purges_oldest_beyond_20(tmp_path, monkeypatch):
    ws = _caged_workspace(tmp_path, monkeypatch)
    tokens = []
    for i in range(21):
        name = f"f{i:02d}.txt"
        (ws / name).write_text(str(i), encoding="utf-8")
        r = files.delete_file(name)
        assert r["ok"] is True, r["message"]
        tokens.append(r["undo_token"])

    trash = files._trash_root()
    dirs = {p.name for p in trash.iterdir() if p.is_dir()}
    assert len(dirs) == 20
    assert tokens[0] not in dirs, "the OLDEST quarantine entry must be purged"
    assert tokens[-1] in dirs


# ---------- 9-11: stack semantics ----------

def test_undo_stack_bounded_to_max_5_entries():
    for i in range(6):
        undo_stack.push(UndoEntry(
            tool=f"t{i}", description=f"entry {i}",
            undo_fn=lambda: {"ok": True, "message": ""}))
    assert len(undo_stack) == 5
    assert undo_stack.pop().tool == "t5"   # newest first (LIFO)…
    for expect in ("t4", "t3", "t2", "t1"):
        assert undo_stack.pop().tool == expect
    assert undo_stack.pop() is None        # …and t0 was displaced by the cap


def test_undo_with_empty_stack_reports_honest_nothing_to_undo():
    brain = JarvisBrain()
    out = brain._execute_tool("undo_last_action", {})
    assert "nothing to undo" in out.lower()
    assert not out.startswith("FAILED")


def test_undo_walks_back_multiple_actions_in_lifo_order(monkeypatch):
    audio = _FakeAudio(level=40)
    _wire_audio(monkeypatch, audio)
    brain = JarvisBrain()

    primitives._run_set_volume({"level": 15})
    primitives._run_set_volume({"level": 7})
    assert audio.level == 7

    out = brain._execute_tool("undo_last_action", {})
    assert out.startswith("OK"), out
    assert audio.level == 15

    out = brain._execute_tool("undo_last_action", {})
    assert out.startswith("OK"), out
    assert audio.level == 40


# ---------- 12: the scope boundary, pinned ----------

def test_media_key_close_tabs_send_email_never_pushed_to_undo_stack(monkeypatch):
    """Irreversible verbs must NEVER appear undoable — a fake undo is worse
    than none. This test pins the boundary the plan drew."""
    from jarvis.primitives import tabs
    from jarvis.primitives import email as jemail

    monkeypatch.setattr(system, "media_key",
                        lambda key: {"ok": True, "message": f"Sent '{key}'."})
    primitives._run_media_key({"key": "play_pause"})

    monkeypatch.setattr(tabs, "close_tabs",
                        lambda **kw: {"ok": True, "message": "Closed 2 tab(s)."})
    primitives._run_close_tabs({"window": "chrome", "keep_matching": "youtube"})

    monkeypatch.setattr(jemail, "send_email_checked",
                        lambda args: {"ok": True, "message": "accepted (id x)."})
    primitives._run_send_email({"to": "a@b.c", "subject": "s", "body": "b"})

    assert undo_stack.pop() is None, \
        "no irreversible verb may land on the undo stack"
