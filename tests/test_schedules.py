"""Slice 50 — scheduled routines: JARVIS acting without being asked.

The cardinal rule these tests exist to protect: **an unattended agent must never
approve itself.** A scheduled run sets `tracker.unattended`, and any step
resolving to a non-AUTO tier is PARKED — not executed, and not prompted at an
empty room. Everything else here (due-calculation, missed runs, guards) is about
not surprising the user: never twice, never hours late, never mid-game.

All time logic uses an injected clock. No sleeping, no wall-clock dependence.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from jarvis.core import schedules as S

MON_8AM = datetime(2026, 8, 3, 8, 0)      # a Monday
MON_8_05 = datetime(2026, 8, 3, 8, 5)
MON_6PM = datetime(2026, 8, 3, 18, 0)
SAT_8AM = datetime(2026, 8, 8, 8, 0)      # a Saturday
TUE_8AM = datetime(2026, 8, 4, 8, 0)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A store on a temp file, with the routine-existence check stubbed TRUE.

    add() validates that the routine exists (a schedule pointing at nothing is
    useless), which would otherwise couple every test here to the real routine
    store on disk. The one test that cares about rejection stubs it False."""
    monkeypatch.setattr(S, "routine_exists", lambda name: True)
    return S.ScheduleStore(tmp_path / "schedules.bin")


# ---------------------------------------------------------------- persistence
def test_schedule_round_trips_through_disk(store):
    store.add("work mode", kind="daily", at="08:00")
    reloaded = S.ScheduleStore(store.path)
    got = reloaded.all()
    assert len(got) == 1
    assert got[0]["routine"] == "work mode" and got[0]["at"] == "08:00"


def test_schedules_are_encrypted_at_rest(store):
    """A schedule names a routine and a time — when the user is predictably
    away from the keyboard. Same DPAPI rule as routines and memory."""
    store.add("work mode", kind="daily", at="08:00")
    raw = store.path.read_bytes()
    assert b"work mode" not in raw
    with pytest.raises(Exception):
        json.loads(raw.decode("utf-8", "ignore"))


def test_add_refuses_when_dpapi_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "routine_exists", lambda name: True)
    monkeypatch.setattr(S.dpapi, "available", lambda: False)
    st = S.ScheduleStore(tmp_path / "s.bin")
    with pytest.raises(RuntimeError, match="secure storage"):
        st.add("work mode", kind="daily", at="08:00")


def test_a_corrupt_store_loads_empty_instead_of_crashing(tmp_path):
    p = tmp_path / "s.bin"
    p.write_bytes(b"not a dpapi blob")
    assert S.ScheduleStore(p).all() == []


# ---------------------------------------------------------------- due logic
def test_daily_is_due_at_its_time(store):
    store.add("work mode", kind="daily", at="08:00")
    assert [d["routine"] for d in store.due(MON_8AM)] == ["work mode"]


def test_not_due_before_its_time(store):
    store.add("work mode", kind="daily", at="08:00")
    assert store.due(datetime(2026, 8, 3, 7, 59)) == []


def test_weekdays_skips_the_weekend(store):
    store.add("work mode", kind="weekdays", at="08:00")
    assert store.due(MON_8AM), "Monday must fire"
    assert store.due(SAT_8AM) == [], "Saturday must not"


def test_weekly_fires_only_on_its_weekday(store):
    store.add("backup", kind="weekly", at="08:00", weekday=0)   # Monday
    assert store.due(MON_8AM), "its weekday must fire"
    assert store.due(TUE_8AM) == [], "another weekday must not"


def test_a_schedule_is_not_due_twice_in_the_same_window(store):
    """RISK 3: a 60s tick must not fire the same 8am job sixty times."""
    store.add("work mode", kind="daily", at="08:00")
    assert store.due(MON_8AM)
    store.mark_ran("work mode", MON_8AM)
    assert store.due(MON_8_05) == [], "already ran in this window"


def test_it_is_due_again_the_next_day(store):
    store.add("work mode", kind="daily", at="08:00")
    store.mark_ran("work mode", MON_8AM)
    assert store.due(TUE_8AM), "a new day is a new window"


def test_a_missed_run_beyond_the_grace_period_is_skipped_not_replayed(store):
    """RISK 4: the PC wakes at 6pm — 8am's routine must NOT fire then."""
    store.add("work mode", kind="daily", at="08:00")
    assert store.due(MON_6PM, grace_minutes=60) == [], \
        "10 hours late is not 'due', it is missed"


def test_within_the_grace_period_it_still_runs(store):
    store.add("work mode", kind="daily", at="08:00")
    assert store.due(datetime(2026, 8, 3, 8, 30), grace_minutes=60)


def test_a_clock_jumping_backwards_does_not_double_fire(store):
    store.add("work mode", kind="daily", at="08:00")
    store.mark_ran("work mode", MON_8_05)
    assert store.due(MON_8AM) == [], "an earlier 'now' must not re-fire it"


# ---------------------------------------------------------------- validation
def test_scheduling_an_unknown_routine_is_rejected(store, monkeypatch):
    monkeypatch.setattr(S, "routine_exists", lambda name: False)
    with pytest.raises(ValueError, match="no routine"):
        store.add("nope", kind="daily", at="08:00")


@pytest.mark.parametrize("bad", ["", "25:00", "8am", "08:60", "abc"])
def test_a_malformed_time_is_rejected(store, bad):
    with pytest.raises(ValueError, match="time"):
        store.add("work mode", kind="daily", at=bad)


def test_an_unknown_kind_is_rejected(store):
    with pytest.raises(ValueError, match="kind"):
        store.add("work mode", kind="hourly", at="08:00")


def test_schedule_count_is_capped(store, monkeypatch):
    monkeypatch.setattr(S, "MAX_SCHEDULES", 2)
    store.add("work mode", kind="daily", at="08:00")
    store.add("work mode", kind="daily", at="09:00")
    with pytest.raises(ValueError, match="2"):
        store.add("work mode", kind="daily", at="10:00")


def test_cancel_removes_only_that_schedule(store):
    a = store.add("work mode", kind="daily", at="08:00")
    store.add("work mode", kind="daily", at="18:00")
    assert store.cancel(a["id"]) is True
    assert [s["at"] for s in store.all()] == ["18:00"]


def test_cancelling_something_absent_is_false_not_an_error(store):
    assert store.cancel("nope") is False


# ================= the cardinal rule: unattended never self-approves =========

def _fake_tool(monkeypatch, name, tier, record):
    from jarvis import primitives
    entry = {"fn": lambda a, g=None: record.append(name) or "OK: ran",
             "tier": tier,
             "schema": {"name": name, "description": "t",
                        "parameters": {"type": "object", "properties": {}}}}
    if tier == "confirm":
        entry["describe"] = lambda a, _n=name: "do " + _n
    monkeypatch.setitem(primitives.PRIMITIVES, name, entry)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from jarvis.core import chain
    from jarvis.state import AgentState, broadcaster
    chain.clear("done")
    yield
    chain.clear("done")
    broadcaster.set(AgentState.IDLE)


def test_unattended_auto_steps_still_run_normally(monkeypatch):
    from jarvis import primitives
    from jarvis.core import chain
    ran = []
    _fake_tool(monkeypatch, "t_auto", "auto", ran)
    chain.start(unattended=True)
    out = primitives.execute("t_auto", {})
    assert ran == ["t_auto"], "AUTO work is exactly what a schedule is for"
    assert out.startswith("OK")


def test_unattended_parks_a_confirm_step_instead_of_running_it(monkeypatch):
    from jarvis import primitives
    from jarvis.core import chain
    ran = []
    _fake_tool(monkeypatch, "t_risky", "confirm", ran)
    chain.start(unattended=True)
    out = primitives.execute("t_risky", {})
    assert ran == [], "a CONFIRM step must NOT run unattended"
    assert out.startswith("PARKED"), out


def test_unattended_never_emits_a_confirm_request(monkeypatch):
    """RISK 1, the cardinal rule. Prompting an empty room is not a gate — it is
    a 30s timeout and a modal nobody sees. It must not even be raised."""
    from jarvis import primitives
    from jarvis.core import chain
    from jarvis.core.confirmations import confirmations
    seen = []
    unsub = confirmations.subscribe(
        lambda e: seen.append(e) if e.get("type") == "confirm_request" else None)
    _fake_tool(monkeypatch, "t_risky", "confirm", [])
    chain.start(unattended=True)
    try:
        primitives.execute("t_risky", {})
    finally:
        unsub()
    assert seen == [], f"unattended must never prompt: {seen}"


def test_park_message_names_the_step_and_says_why(monkeypatch):
    from jarvis import primitives
    from jarvis.core import chain
    _fake_tool(monkeypatch, "t_risky", "confirm", [])
    chain.start(unattended=True)
    out = primitives.execute("t_risky", {})
    assert "t_risky" in out or "do t_risky" in out, out
    assert "approval" in out.lower() or "confirm" in out.lower(), out


def test_attended_runs_are_unaffected(monkeypatch):
    """No regression: a normal interaction still gates rather than parking."""
    import threading

    from jarvis import primitives
    from jarvis.core import chain
    from jarvis.core.confirmations import confirmations
    seen = []

    def responder(e):
        if e.get("type") == "confirm_request":
            seen.append(e)
            # Answer it. Without this the test waits out the real 30s
            # confirm.timeout_s — one sleepy test is how a suite gets slow, and
            # slice 45 already made gate minutes expensive.
            threading.Thread(
                target=lambda: confirmations.resolve(e["id"], False)).start()

    unsub = confirmations.subscribe(responder)
    _fake_tool(monkeypatch, "t_risky", "confirm", [])
    chain.start()                      # attended
    try:
        out = primitives.execute("t_risky", {})
    finally:
        unsub()
    assert seen, "an attended CONFIRM must still prompt"
    assert not out.startswith("PARKED"), out


def test_blocked_still_beats_parked(monkeypatch):
    """A BLOCKED verb must stay blocked unattended — parking is for CONFIRM."""
    from jarvis import primitives
    from jarvis.core import chain
    monkeypatch.setitem(primitives.PRIMITIVES, "t_blocked", {
        "fn": lambda a, g=None: "OK: ran",
        "classify": lambda a: {"tier": "blocked", "description": "BLOCKED: no."},
        "schema": {"name": "t_blocked", "description": "t",
                   "parameters": {"type": "object", "properties": {}}}})
    chain.start(unattended=True)
    out = primitives.execute("t_blocked", {})
    assert out.startswith("BLOCKED"), out


# ==================== stage 2: the runner and its guards ====================
# The guards exist so a scheduled run never SURPRISES the user: not while they
# are mid-interaction, not while they are gaming, not twice, not hours late.

def test_conftest_and_product_share_one_fullscreen_check():
    """There must be ONE definition of 'the screen is claimed'. It lived only in
    tests/conftest.py, which is exactly why the product had no guard at all."""
    from jarvis.core import desktop
    import tests.conftest as ct
    assert ct._SCREEN_CLAIMED_STATES == desktop.SCREEN_CLAIMED_STATES, \
        "conftest must reuse the product's definition, not keep a copy"


def test_screen_is_claimed_reads_the_state(monkeypatch):
    from jarvis.core import desktop
    for state, claimed in ((2, True), (3, True), (4, True), (5, False), (1, False)):
        monkeypatch.setenv(desktop.FAKE_ENV, str(state))
        assert desktop.screen_is_claimed() is claimed, f"state {state}"


def test_screen_check_never_raises(monkeypatch):
    from jarvis.core import desktop
    monkeypatch.setenv(desktop.FAKE_ENV, "not-a-number")
    assert desktop.screen_is_claimed() is False, "garbage must read as free"


def _sched_env(monkeypatch, tmp_path, when):
    """A store with one schedule due at `when`, wired into the server module."""
    from jarvis import server
    from jarvis.core import schedules as SS
    monkeypatch.setattr(SS, "routine_exists", lambda name: True)
    st = SS.ScheduleStore(tmp_path / "s.bin")
    st.add("work mode", kind="daily", at=when.strftime("%H:%M"))
    monkeypatch.setattr(server, "schedule_store", st, raising=False)
    return server, st


def test_scheduler_runs_a_due_schedule(monkeypatch, tmp_path):
    from datetime import datetime
    now = datetime(2026, 8, 3, 8, 0)
    server, st = _sched_env(monkeypatch, tmp_path, now)
    monkeypatch.setattr(server.desktop, "screen_is_claimed", lambda: False)
    ran = []
    monkeypatch.setattr(server, "_run_scheduled", lambda rec: ran.append(rec["routine"]))
    server._scheduler_tick(now)
    assert ran == ["work mode"]


def test_scheduler_skips_when_busy(monkeypatch, tmp_path):
    """The user is mid-conversation. A routine barging in is worse than late."""
    from datetime import datetime
    now = datetime(2026, 8, 3, 8, 0)
    server, st = _sched_env(monkeypatch, tmp_path, now)
    monkeypatch.setattr(server.desktop, "screen_is_claimed", lambda: False)
    ran = []
    monkeypatch.setattr(server, "_run_scheduled", lambda rec: ran.append(rec))
    assert server._busy.acquire(blocking=False)
    try:
        server._scheduler_tick(now)
    finally:
        server._busy.release()
    assert ran == [], "must not run while an interaction is in flight"
    assert st.all()[0]["last_run"] is None, "a skip must NOT consume the window"


def test_scheduler_skips_when_fullscreen(monkeypatch, tmp_path):
    """RISK 2: launching apps mid-race is worse than not running at all."""
    from datetime import datetime
    now = datetime(2026, 8, 3, 8, 0)
    server, st = _sched_env(monkeypatch, tmp_path, now)
    monkeypatch.setattr(server.desktop, "screen_is_claimed", lambda: True)
    ran = []
    monkeypatch.setattr(server, "_run_scheduled", lambda rec: ran.append(rec))
    server._scheduler_tick(now)
    assert ran == [], "must not steal focus from a fullscreen app"
    assert st.all()[0]["last_run"] is None, "a skip must NOT consume the window"


def test_last_run_is_stamped_before_execution(monkeypatch, tmp_path):
    """RISK 3: if the run crashes, the job must still not re-fire next tick."""
    from datetime import datetime
    now = datetime(2026, 8, 3, 8, 0)
    server, st = _sched_env(monkeypatch, tmp_path, now)
    monkeypatch.setattr(server.desktop, "screen_is_claimed", lambda: False)
    seen = {}

    def boom(rec):
        seen["stamped_at_run_time"] = st.all()[0]["last_run"]
        raise RuntimeError("the routine exploded")
    monkeypatch.setattr(server, "_run_scheduled", boom)
    server._scheduler_tick(now)
    assert seen["stamped_at_run_time"] is not None, \
        "last_run must be stamped BEFORE the routine runs"
    assert st.due(datetime(2026, 8, 3, 8, 1)) == [], "and must not re-fire"


def test_scheduler_survives_a_failing_run(monkeypatch, tmp_path):
    """RISK 8: a dead scheduler thread takes the feature down invisibly."""
    from datetime import datetime
    now = datetime(2026, 8, 3, 8, 0)
    server, st = _sched_env(monkeypatch, tmp_path, now)
    monkeypatch.setattr(server.desktop, "screen_is_claimed", lambda: False)

    def boom(rec):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(server, "_run_scheduled", boom)
    server._scheduler_tick(now)          # must not propagate
    server._scheduler_tick(datetime(2026, 8, 4, 8, 0))   # still ticking


def test_scheduler_does_nothing_when_disabled(monkeypatch, tmp_path):
    from datetime import datetime
    from jarvis.core.settings_store import settings
    now = datetime(2026, 8, 3, 8, 0)
    server, st = _sched_env(monkeypatch, tmp_path, now)
    monkeypatch.setattr(server.desktop, "screen_is_claimed", lambda: False)
    ran = []
    monkeypatch.setattr(server, "_run_scheduled", lambda rec: ran.append(rec))
    real = settings.get
    monkeypatch.setattr(settings, "get", lambda p, d=None:
                        (False if p == "schedules.enabled" else real(p, d)))
    server._scheduler_tick(now)
    assert ran == []
