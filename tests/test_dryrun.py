"""Slice 18 — dry-run mode (spec §1.4 "plan + narrate without executing").

The guarantee is MECHANICAL, never prompt-trusted: the per-think tracker
carries dry_run, and primitives.execute() returns a narration before the
gate and before the fn — so in a dry run zero primitives execute, zero
CONFIRM modals appear, and zero memory mutations land. Dry-runs are
themselves audited (dry_run=true).
"""
from __future__ import annotations

import os

import pytest

from jarvis import primitives
from jarvis.core import audit, chain
from jarvis.core.confirmations import confirmations as _confirmations


@pytest.fixture(autouse=True)
def _clean_chain_and_state():
    """Leak guards: no test may leave a live tracker or a non-IDLE
    broadcaster behind (same doctrine as test_chain/test_audit)."""
    yield
    if chain.current() is not None:
        chain.clear("done")
    from jarvis.state import AgentState, broadcaster
    broadcaster.set(AgentState.IDLE)


@pytest.fixture()
def dry_tracker():
    tracker = chain.start(dry_run=True)
    yield tracker
    chain.clear("done")


@pytest.fixture()
def no_modal(monkeypatch):
    """The CONFIRM gate must NEVER be reached in a dry run."""
    def boom(*_a, **_k):
        raise AssertionError("confirmations.request was called during a dry run")
    monkeypatch.setattr(_confirmations, "request", boom)


@pytest.fixture()
def no_exec(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("a subprocess was spawned during a dry run")
    monkeypatch.setattr("subprocess.Popen", boom)
    monkeypatch.setattr("subprocess.run", boom)


# ---------------------------------------------------------------- test 16
def test_prefix_parse_variants():
    from jarvis.server import parse_dry_run
    for raw in ("dry run: open notepad", "Dry-run open notepad",
                "DRY RUN, open notepad", "  dryrun: open notepad"):
        dry, text = parse_dry_run(raw)
        assert dry is True, raw
        assert text == "open notepad", (raw, text)
    for raw in ("open notepad", "do a dry run of opening notepad",
                "drying the laundry run"):
        dry, text = parse_dry_run(raw)
        assert dry is False, raw
        assert text == raw


# ---------------------------------------------------------------- test 17
def test_dry_auto_primitive_not_executed(dry_tracker, monkeypatch):
    def boom(_args, _gate_info=None):
        raise AssertionError("primitive fn ran during a dry run")
    monkeypatch.setitem(primitives.PRIMITIVES["launch_app"], "fn", boom)
    out = primitives.execute("launch_app", {"name": "notepad"})
    assert out.startswith("DRY RUN"), out
    assert "not executed" in out
    assert "launch_app" in out


# ---------------------------------------------------------------- test 18
def test_dry_confirm_never_shows_modal(dry_tracker, no_modal, monkeypatch):
    def boom(_args, _gate_info=None):
        raise AssertionError("primitive fn ran during a dry run")
    monkeypatch.setitem(primitives.PRIMITIVES["delete_file"], "fn", boom)
    out = primitives.execute("delete_file", {"name": "a.txt"})
    assert out.startswith("DRY RUN"), out
    assert "confirmation" in out.lower()   # narrates the would-be gate


# ---------------------------------------------------------------- test 19
def test_dry_shell_denylist_still_blocked(dry_tracker, no_modal, no_exec):
    """run_shell's classifier is argument-complete, so a dry run still
    reports the denylist verdict honestly — BLOCKED, not narrated-as-fine."""
    out = primitives.execute("run_shell", {"command": "rmdir /s /q C:\\"})
    assert out.startswith("BLOCKED"), out
    # ...and a benign command narrates the would-be verbatim confirm.
    out2 = primitives.execute("run_shell", {"command": "echo hello"})
    assert out2.startswith("DRY RUN"), out2
    assert "confirmation" in out2.lower()


# ---------------------------------------------------------------- test 20
def test_dry_remember_does_not_mutate(dry_tracker, tmp_path):
    from jarvis.brain import JarvisBrain
    from jarvis.core.memory import MemoryStore
    brain = JarvisBrain()
    brain.memory = MemoryStore(tmp_path / "mem" / "memories.bin")
    out = brain._memory_tool("remember", {"text": "the user prefers tea"})
    assert out.startswith("DRY RUN"), out
    assert brain.memory.all() == []        # nothing landed
    out2 = brain._memory_tool("forget", {"query": "tea"})
    assert out2.startswith("DRY RUN"), out2


# ---------------------------------------------------------------- test 21
def test_dry_runs_are_audited(dry_tracker, monkeypatch):
    monkeypatch.setitem(primitives.PRIMITIVES["launch_app"], "fn",
                        lambda *_a, **_k: pytest.fail("must not run"))
    primitives.execute("launch_app", {"name": "notepad"})
    (rec,) = audit.audit_log.read()
    assert rec["dry_run"] is True
    assert rec["tool"] == "launch_app"
    assert rec["status"] == "ok"
    assert rec["chain"] == dry_tracker.chain_id


# ---------------------------------------------------------------- test 22
def test_dry_flag_never_leaks(monkeypatch):
    tracker = chain.start(dry_run=True)
    chain.clear("done")
    assert chain.current() is None
    ran = []
    monkeypatch.setitem(primitives.PRIMITIVES["launch_app"], "fn",
                        lambda args, gi=None: ran.append(args) or "OK: ran.")
    out = primitives.execute("launch_app", {"name": "notepad"})
    assert out.startswith("OK")
    assert ran == [{"name": "notepad"}], "post-dry execute must really run"
    # And a fresh normal tracker is not dry.
    t2 = chain.start()
    assert t2.dry_run is False
    chain.clear("done")


# ---------------------------------------------------------------- test 23
def test_plan_steps_and_recall_work_in_dry(dry_tracker, tmp_path):
    from jarvis.brain import JarvisBrain
    from jarvis.core.memory import MemoryStore
    brain = JarvisBrain()
    brain.memory = MemoryStore(tmp_path / "mem" / "memories.bin")
    brain.memory.add("the user prefers tea")
    out = brain._plan_steps({"steps": ["open notepad", "type hello"]})
    assert out.startswith("PLAN SET"), out
    assert dry_tracker.steps == ["open notepad", "type hello"]
    out2 = brain._memory_tool("recall", {})
    assert out2.startswith("OK") and "tea" in out2   # read-only, runs for real


# ---------------------------------------------------------------- test 24
def test_server_respond_passes_dry_flag(monkeypatch):
    from jarvis import server
    calls = {}
    def fake_think(text, dry_run=False):
        calls["text"], calls["dry_run"] = text, dry_run
        return "narrated"
    monkeypatch.setattr(server.jarvis_brain, "think", fake_think)
    monkeypatch.setattr(server.voice_manager, "speak", lambda _t: None)
    reply = server._respond("dry run: open notepad")
    assert reply == "narrated"
    assert calls == {"text": "open notepad", "dry_run": True}
    server._respond("open notepad")
    assert calls == {"text": "open notepad", "dry_run": False}


# ---------------------------------------------------------------- test 25
@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"),
                    reason="live model test needs GEMINI_API_KEY")
def test_live_dry_run_notepad(monkeypatch):
    """Live acceptance: the real model plans a dry run; MECHANICAL proof that
    nothing executed — no new Notepad process, every audit record dry."""
    import psutil
    from jarvis.brain import jarvis_brain

    def notepads() -> set[int]:
        return {p.pid for p in psutil.process_iter(["name"])
                if (p.info["name"] or "").lower().startswith("notepad")}

    before = notepads()
    reply = jarvis_brain.think("dry run: open notepad and type hello", dry_run=True)
    assert notepads() == before, "a real Notepad appeared during a dry run"
    assert isinstance(reply, str) and reply.strip()
    records = audit.audit_log.read()
    assert records, "the model called no tools at all — dry run proved nothing"
    assert all(r["dry_run"] is True for r in records)
