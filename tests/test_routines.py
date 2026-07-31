"""Slice 48 — routines: a named, saved chain the user replays by name.

The store's job is to hold STEPS, not authority: nothing here decides whether a
step may run. That stays with primitives.execute(), which re-gates every step at
RUN time (kill switch -> tier -> CONFIRM -> audit). These tests pin the store's
own contract — persistence, validation, and the two things that would be
dangerous if wrong: recursion and unbounded growth.
"""
from __future__ import annotations

import json

import pytest

from jarvis.core import routines as R


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A RoutineStore on a temp file — never the user's real routines."""
    return R.RoutineStore(tmp_path / "routines.bin")


WORK = [{"tool": "launch_app", "args": {"name": "notepad"}},
        {"tool": "set_mute", "args": {"muted": True}}]


# ---------------------------------------------------------------- persistence
def test_save_and_get_round_trips_through_disk(store):
    store.save("work mode", WORK)
    reloaded = R.RoutineStore(store.path)          # fresh instance, same file
    got = reloaded.get("work mode")
    assert got is not None, "a saved routine must survive a reload"
    assert got["steps"] == WORK
    assert got["name"] == "work mode"


def test_routines_are_encrypted_at_rest(store):
    """Routines hold app names, URLs and file paths — personal data. The bytes
    on disk must not be readable JSON (same rule as MemoryStore)."""
    store.save("work mode", WORK)
    raw = store.path.read_bytes()
    assert b"notepad" not in raw, "routine contents must not sit in plaintext"
    with pytest.raises(Exception):
        json.loads(raw.decode("utf-8", "ignore"))


def test_save_refuses_when_dpapi_is_unavailable(tmp_path, monkeypatch):
    """Refuse rather than fall back to plaintext — MemoryStore._persist's rule."""
    monkeypatch.setattr(R.dpapi, "available", lambda: False)
    s = R.RoutineStore(tmp_path / "routines.bin")
    with pytest.raises(RuntimeError, match="secure storage"):
        s.save("work mode", WORK)
    assert not (tmp_path / "routines.bin").exists(), "nothing may be written"


def test_a_corrupt_store_loads_empty_instead_of_crashing(tmp_path):
    p = tmp_path / "routines.bin"
    p.write_bytes(b"not a dpapi blob")
    assert R.RoutineStore(p).all() == []


# ---------------------------------------------------------------- name matching
@pytest.mark.parametrize("lookup", ["work mode", "Work Mode", "  WORK   mode ",
                                    "WoRk MoDe"])
def test_name_matching_is_case_and_whitespace_insensitive(store, lookup):
    store.save("work mode", WORK)
    assert store.get(lookup) is not None, f"{lookup!r} must find 'work mode'"


def test_saving_the_same_name_replaces_rather_than_duplicates(store):
    store.save("work mode", WORK)
    store.save("WORK MODE", [{"tool": "set_dnd", "args": {"on": True}}])
    assert len(store.all()) == 1, "same normalized name = one routine"
    assert store.get("work mode")["steps"][0]["tool"] == "set_dnd"


def test_exists_reports_a_name_collision_before_saving(store):
    store.save("work mode", WORK)
    assert store.exists("Work  Mode") is True
    assert store.exists("wind down") is False


def test_unknown_name_returns_none_not_a_guess(store):
    store.save("work mode", WORK)
    assert store.get("wind down") is None, "must not fuzzy-match a different routine"


# ---------------------------------------------------------------- validation
def test_a_routine_with_no_steps_is_rejected(store):
    with pytest.raises(ValueError, match="at least one step"):
        store.save("empty", [])


@pytest.mark.parametrize("name", ["", "   ", "\n\t"])
def test_a_blank_name_is_rejected(store, name):
    with pytest.raises(ValueError, match="name"):
        store.save(name, WORK)


def test_step_with_an_unknown_tool_is_rejected(store):
    with pytest.raises(ValueError, match="unknown tool"):
        store.save("bad", [{"tool": "definitely_not_a_tool", "args": {}}])


def test_a_malformed_step_is_rejected(store):
    for bad in ([{"args": {}}], ["not a dict"], [{"tool": 5}], [None]):
        with pytest.raises(ValueError):
            store.save("bad", bad)


def test_a_routine_containing_run_routine_is_rejected(store):
    """RECURSION. A routine that runs a routine is an unbounded loop; refuse it
    at the door rather than relying on a depth counter at run time."""
    with pytest.raises(ValueError, match="another routine"):
        store.save("loop", [{"tool": "run_routine", "args": {"name": "loop"}}])


def test_step_count_is_capped(store, monkeypatch):
    monkeypatch.setattr(R, "MAX_STEPS", 3)
    with pytest.raises(ValueError, match="3"):
        store.save("long", [{"tool": "read_ui_tree", "args": {}}] * 4)


def test_routine_count_is_capped(store, monkeypatch):
    monkeypatch.setattr(R, "MAX_ROUTINES", 2)
    store.save("one", WORK)
    store.save("two", WORK)
    with pytest.raises(ValueError, match="2"):
        store.save("three", WORK)
    # replacing an EXISTING routine at the cap must still work
    store.save("one", [{"tool": "set_dnd", "args": {"on": True}}])


def test_name_length_is_capped(store):
    with pytest.raises(ValueError, match="too long"):
        store.save("x" * 200, WORK)


# ---------------------------------------------------------------- delete / list
def test_delete_removes_only_the_named_routine(store):
    store.save("work mode", WORK)
    store.save("wind down", [{"tool": "set_dnd", "args": {"on": True}}])
    assert store.delete("WORK mode") is True
    assert store.get("work mode") is None
    assert store.get("wind down") is not None


def test_deleting_something_that_does_not_exist_is_false_not_an_error(store):
    assert store.delete("nope") is False


def test_all_returns_routines_in_a_stable_order(store):
    store.save("b", WORK)
    store.save("a", WORK)
    assert [r["name"] for r in R.RoutineStore(store.path).all()] == ["a", "b"]


def test_names_helper_lists_saved_names(store):
    store.save("work mode", WORK)
    store.save("wind down", WORK)
    assert store.names() == ["wind down", "work mode"] or \
           store.names() == ["work mode", "wind down"]


# ---------------------------------------------------------------- hostile
def test_a_hand_edited_file_with_a_bad_step_is_caught_at_read(store):
    """Validation at save can be bypassed by editing the file. valid_steps()
    is the run-time re-check that must catch it."""
    ok, why = R.valid_steps([{"tool": "run_routine", "args": {"name": "x"}}])
    assert ok is False and "another routine" in why
    ok, why = R.valid_steps([{"tool": "nope", "args": {}}])
    assert ok is False and "unknown tool" in why
    ok, _ = R.valid_steps(WORK)
    assert ok is True


# ==================== stage 2: the primitives ====================
# The point of this block: a routine confers NO authority. Every step is
# re-gated by primitives.execute() at RUN time, so a CONFIRM step still prompts
# on every run and declining it kills the rest of the routine.

import threading
import time

from jarvis import primitives
from jarvis.core import chain
from jarvis.core.confirmations import confirmations


@pytest.fixture(autouse=True)
def _broadcaster_back_to_idle():
    """LEAK GUARD — the same one test_audit.py carries, for the same reason.

    primitives.execute() called OUTSIDE think() deliberately parks the
    broadcaster at THINKING (think()'s finally normally restores IDLE). This
    file drives execute() directly all through the stage-2 block, and it sorts
    before test_server.py, so without this reset it leaks THINKING into
    test_server::test_state_endpoint's `state == "idle"` assertion.

    It was invisible until slice 49 ran a deterministic-ONLY gate: the live
    tests in between were quietly resetting the state by calling think().
    """
    yield
    from jarvis.state import AgentState, broadcaster
    broadcaster.set(AgentState.IDLE)


@pytest.fixture(autouse=True)
def _temp_routine_store(tmp_path, monkeypatch):
    """Never touch the user's real routines from a test."""
    st = R.RoutineStore(tmp_path / "r.bin")
    monkeypatch.setattr(primitives, "routine_store", st, raising=False)
    monkeypatch.setattr(R, "routine_store", st, raising=False)
    return st


def _auto_resolve(approved: bool):
    def responder(event):
        if event.get("type") == "confirm_request":
            threading.Thread(target=lambda: (
                time.sleep(0.05),
                confirmations.resolve(event["id"], approved))).start()
    return confirmations.subscribe(responder)


def _fake_tool(monkeypatch, name, tier="auto", record=None):
    """Register a throwaway primitive so these tests never drive the real PC."""
    def fn(args, gate_info=None):
        if record is not None:
            record.append((name, dict(args or {})))
        return "OK: " + name + " ran"
    entry = {"fn": fn, "tier": tier,
             "schema": {"name": name, "description": "test tool",
                        "parameters": {"type": "object", "properties": {}}}}
    if tier == "confirm":
        entry["describe"] = lambda a, _n=name: "do " + _n
    monkeypatch.setitem(primitives.PRIMITIVES, name, entry)


# ---------------------------------------------------------------- run
def test_run_routine_executes_every_step_in_order(monkeypatch, _temp_routine_store):
    ran = []
    _fake_tool(monkeypatch, "t_one", record=ran)
    _fake_tool(monkeypatch, "t_two", record=ran)
    _temp_routine_store.save("work mode", [{"tool": "t_one", "args": {"a": 1}},
                                           {"tool": "t_two", "args": {}}])
    out = primitives.execute("run_routine", {"name": "work mode"})
    assert [n for n, _ in ran] == ["t_one", "t_two"], "order wrong: " + str(ran)
    assert ran[0][1] == {"a": 1}, "stored args must be replayed verbatim"
    assert out.startswith("OK"), out


def test_run_routine_goes_through_execute_so_a_confirm_step_prompts(
        monkeypatch, _temp_routine_store):
    """The central safety claim of this slice."""
    ran = []
    _fake_tool(monkeypatch, "t_safe", record=ran)
    _fake_tool(monkeypatch, "t_risky", tier="confirm", record=ran)
    _temp_routine_store.save("mixed", [{"tool": "t_safe", "args": {}},
                                       {"tool": "t_risky", "args": {}}])
    seen = []
    unsub = confirmations.subscribe(
        lambda e: seen.append(e) if e.get("type") == "confirm_request" else None)
    approve = _auto_resolve(True)
    try:
        primitives.execute("run_routine", {"name": "mixed"})
    finally:
        unsub()
        approve()
    assert seen, "a CONFIRM step inside a routine MUST still prompt"
    assert [n for n, _ in ran] == ["t_safe", "t_risky"]


def test_declining_a_step_aborts_the_rest_of_the_routine(
        monkeypatch, _temp_routine_store):
    """Saying no to step 2 must not let step 3 run."""
    ran = []
    _fake_tool(monkeypatch, "t_a", record=ran)
    _fake_tool(monkeypatch, "t_risky", tier="confirm", record=ran)
    _fake_tool(monkeypatch, "t_c", record=ran)
    _temp_routine_store.save("three", [{"tool": "t_a", "args": {}},
                                       {"tool": "t_risky", "args": {}},
                                       {"tool": "t_c", "args": {}}])
    decline = _auto_resolve(False)
    try:
        out = primitives.execute("run_routine", {"name": "three"})
    finally:
        decline()
        chain.clear("done")
    assert [n for n, _ in ran] == ["t_a"], "step 3 must NOT run: " + str(ran)
    assert "step 2" in out, "must name the step that stopped it: " + out


def test_run_routine_stops_and_names_the_failing_step(monkeypatch, _temp_routine_store):
    ran = []
    _fake_tool(monkeypatch, "t_ok", record=ran)
    monkeypatch.setitem(primitives.PRIMITIVES, "t_bad", {
        "fn": lambda a, g=None: "FAILED: nope", "tier": "auto",
        "schema": {"name": "t_bad", "description": "x",
                   "parameters": {"type": "object", "properties": {}}}})
    _fake_tool(monkeypatch, "t_never", record=ran)
    _temp_routine_store.save("brk", [{"tool": "t_ok", "args": {}},
                                     {"tool": "t_bad", "args": {}},
                                     {"tool": "t_never", "args": {}}])
    try:
        out = primitives.execute("run_routine", {"name": "brk"})
    finally:
        chain.clear("done")
    assert [n for n, _ in ran] == ["t_ok"], "must stop at the failure: " + str(ran)
    assert "step 2" in out and "t_bad" in out, out


def test_run_routine_registers_each_step_with_the_chain_tracker(
        monkeypatch, _temp_routine_store):
    """DoD 3: the HUD must show step-by-step progress, not one opaque row.
    brain.py only wraps the OUTER call, so the routine registers its own."""
    _fake_tool(monkeypatch, "t_one")
    _fake_tool(monkeypatch, "t_two")
    _temp_routine_store.save("two step", [{"tool": "t_one", "args": {}},
                                          {"tool": "t_two", "args": {}}])
    tracker = chain.start()
    try:
        primitives.execute("run_routine", {"name": "two step"})
        tools = [c["tool"] for c in tracker.calls]
    finally:
        chain.clear("done")
    assert "t_one" in tools and "t_two" in tools, \
        "each routine step must appear in the Action Log: " + str(tools)


def test_running_an_unknown_routine_is_honest(_temp_routine_store):
    _temp_routine_store.save("work mode", WORK)
    out = primitives.execute("run_routine", {"name": "nope"})
    assert out.startswith("FAILED")
    assert "work mode" in out, "should list what DOES exist"


def test_routine_steps_are_re_checked_for_recursion_at_run_time(
        monkeypatch, _temp_routine_store):
    """save() rejects run_routine, but the file can be hand-edited. The run-time
    re-check is the real boundary."""
    _temp_routine_store.save("ok", [{"tool": "read_ui_tree", "args": {}}])
    _temp_routine_store._records[0]["steps"] = [
        {"tool": "run_routine", "args": {"name": "ok"}}]
    out = primitives.execute("run_routine", {"name": "ok"})
    assert out.startswith("FAILED"), out
    assert "another routine" in out, out


# ---------------------------------------------------------------- save/delete
def test_new_routine_is_auto(_temp_routine_store):
    info = primitives.PRIMITIVES["save_routine"]["classify"](
        {"name": "brand new", "steps": WORK})
    assert info["tier"] == "auto"


def test_saving_over_an_existing_routine_confirms(_temp_routine_store):
    _temp_routine_store.save("work mode", WORK)
    info = primitives.PRIMITIVES["save_routine"]["classify"](
        {"name": "Work Mode", "steps": WORK})
    assert info["tier"] == "confirm", "overwrite must gate (write_file precedent)"
    assert "work mode" in info["description"].lower()


def test_delete_routine_confirms(_temp_routine_store):
    _temp_routine_store.save("work mode", WORK)
    info = primitives.PRIMITIVES["delete_routine"]["classify"]({"name": "work mode"})
    assert info["tier"] == "confirm"


def test_save_routine_rejects_a_bad_step_with_the_reason(_temp_routine_store):
    out = primitives.execute("save_routine",
                             {"name": "bad", "steps": [{"tool": "nope"}]})
    assert out.startswith("FAILED") and "unknown tool" in out


def test_list_routines_reports_names_and_step_counts(_temp_routine_store):
    _temp_routine_store.save("work mode", WORK)
    out = primitives.execute("list_routines", {})
    assert "work mode" in out and "2" in out


def test_list_routines_is_honest_when_empty(_temp_routine_store):
    out = primitives.execute("list_routines", {})
    assert "no routines" in out.lower()


# ---------------------------------------------------------------- kill switch
def test_routines_withheld_from_schema_when_disabled(monkeypatch):
    from jarvis.core.settings_store import settings
    real = settings.get
    monkeypatch.setattr(settings, "get", lambda p, d=None:
                        (False if p == "routines.enabled" else real(p, d)))
    advertised = {s["name"] for s in primitives.tools_schema()}
    assert not ({"save_routine", "run_routine", "list_routines",
                 "delete_routine"} & advertised)


def test_routines_execute_blocked_when_disabled(monkeypatch, _temp_routine_store):
    from jarvis.core.settings_store import settings
    _temp_routine_store.save("work mode", WORK)
    real = settings.get
    monkeypatch.setattr(settings, "get", lambda p, d=None:
                        (False if p == "routines.enabled" else real(p, d)))
    out = primitives.execute("run_routine", {"name": "work mode"})
    assert out.startswith("BLOCKED"), out


# ==================== stage 3: discoverability ====================
# MEASURED load-bearing (Stage 0): with saved names in the prompt the model maps
# a bare "work mode" to run_routine 4/4; without them, 0/4 -- it calls
# list_routines instead. So this block is the feature, not decoration.

def test_saved_routine_names_appear_in_the_prompt(monkeypatch, _temp_routine_store):
    from jarvis.brain import JarvisBrain
    _temp_routine_store.save("work mode", WORK)
    _temp_routine_store.save("wind down", [{"tool": "set_dnd", "args": {"on": True}}])
    monkeypatch.setattr(R, "routine_store", _temp_routine_store)
    block = JarvisBrain()._routines_block()
    assert "work mode" in block and "wind down" in block, block
    assert "run_routine" in block, "must name the tool that runs them"


def test_prompt_block_is_empty_when_there_are_no_routines(monkeypatch,
                                                          _temp_routine_store):
    """An unused feature must add zero prompt noise."""
    from jarvis.brain import JarvisBrain
    monkeypatch.setattr(R, "routine_store", _temp_routine_store)
    assert JarvisBrain()._routines_block() == ""


def test_prompt_block_is_empty_when_routines_are_disabled(monkeypatch,
                                                          _temp_routine_store):
    from jarvis.brain import JarvisBrain
    from jarvis.core.settings_store import settings
    _temp_routine_store.save("work mode", WORK)
    monkeypatch.setattr(R, "routine_store", _temp_routine_store)
    real = settings.get
    monkeypatch.setattr(settings, "get", lambda p, d=None:
                        (False if p == "routines.enabled" else real(p, d)))
    assert JarvisBrain()._routines_block() == ""


def test_the_routines_block_never_breaks_think(monkeypatch):
    """A store failure must never take down the brain -- same rule as memory."""
    from jarvis.brain import JarvisBrain

    class Boom:
        def names(self):
            raise RuntimeError("disk gone")
    monkeypatch.setattr(R, "routine_store", Boom())
    assert JarvisBrain()._routines_block() == ""


def test_the_block_lists_names_only_not_steps(monkeypatch, _temp_routine_store):
    """Steps can be long; the model only needs the name to invoke one."""
    from jarvis.brain import JarvisBrain
    _temp_routine_store.save("work mode", WORK)
    monkeypatch.setattr(R, "routine_store", _temp_routine_store)
    block = JarvisBrain()._routines_block()
    assert "launch_app" not in block and "set_mute" not in block, block


# ---------- a parked step must never be reported as completed ----------
# Caught LIVE, not by unit tests: the scheduled run reported "all 2 steps
# completed (set_volume, run_shell)" while run_shell had been PARKED and never
# ran. The step was safely not executed -- but the REPORT was false, which is
# the half-run-reported-as-success failure the routine contract forbids.

def test_a_parked_step_is_not_counted_as_completed(monkeypatch, _temp_routine_store):
    from jarvis import primitives
    from jarvis.core import chain
    ran = []
    _fake_tool(monkeypatch, "t_auto", record=ran)
    _fake_tool(monkeypatch, "t_risky", tier="confirm", record=ran)
    _temp_routine_store.save("mixed", [{"tool": "t_auto", "args": {}},
                                       {"tool": "t_risky", "args": {}}])
    chain.start(unattended=True)
    try:
        out = primitives.execute("run_routine", {"name": "mixed"})
    finally:
        chain.clear("done")
    assert [n for n, _ in ran] == ["t_auto"],         f"the CONFIRM step must not run unattended: {ran}"
    assert "all 2 steps completed" not in out, f"FALSE report: {out}"
    assert "SKIPPED" in out and "t_risky" in out, out
    assert "did NOT run" in out.lower() or "did not run" in out.lower(), out


def test_parked_steps_do_not_stop_the_rest_of_the_routine(monkeypatch,
                                                          _temp_routine_store):
    """A parked step is not a failure -- the remaining AUTO steps are still
    wanted at 8am. Stopping would silently lose useful work."""
    from jarvis import primitives
    from jarvis.core import chain
    ran = []
    _fake_tool(monkeypatch, "t_risky", tier="confirm", record=ran)
    _fake_tool(monkeypatch, "t_after", record=ran)
    _temp_routine_store.save("after", [{"tool": "t_risky", "args": {}},
                                       {"tool": "t_after", "args": {}}])
    chain.start(unattended=True)
    try:
        out = primitives.execute("run_routine", {"name": "after"})
    finally:
        chain.clear("done")
    assert [n for n, _ in ran] == ["t_after"],         f"steps after a park must still run: {ran}"
    assert "1 of 2" in out, out
