"""Slice 45 — the test-only Gemini pacer. All deterministic: fake clock, no API.

The pacer's whole job is to stop free-tier RPM limits forging test failures. If
IT is wrong, it either doesn't pace (the false failures come back) or it sleeps
forever (the gate hangs). Both are pinned here.
"""
from __future__ import annotations

import pytest

from tests._pacer import (DEFAULT_BUDGET_PER_MIN, ENV_BUDGET, ENV_DISABLE,
                          WINDOW_S, QuotaPacer, budget_from_env, install,
                          uninstall)


@pytest.fixture(autouse=True)
def _preserve_session_pacer():
    """LEAK GUARD — the third bug of this exact class in three slices.

    Several tests below install/uninstall the PROCESS-WIDE SDK patch. Leaving it
    uninstalled disarms pacing for every test that runs after this file
    (alphabetically: search_live, undo_live, vision, web_live), silently
    restoring the false-failure problem this slice exists to kill.

    MEASURED, not hypothetical: the first stage-2 gate reported 36 calls with a
    fallback to gemini-2.5-flash that was never counted, because this file had
    already torn the wrapper off. Snapshot the globals AND the bound method, and
    put them back.
    """
    import tests._pacer as pacer_mod
    from google.genai.models import Models
    saved = (pacer_mod._installed, pacer_mod._original, Models.generate_content)
    yield
    pacer_mod._installed, pacer_mod._original = saved[0], saved[1]
    Models.generate_content = saved[2]


class FakeClock:
    """A clock the test drives. sleep() advances it, so no real waiting."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0, f"never sleep a negative duration ({seconds})"
        self.slept.append(seconds)
        self.t += seconds

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _pacer(budget: int = 3) -> tuple[QuotaPacer, FakeClock]:
    clock = FakeClock()
    return QuotaPacer(budget, clock=clock.now, sleeper=clock.sleep), clock


# ---------------------------------------------------------------- budget
def test_calls_under_the_budget_never_sleep():
    pacer, clock = _pacer(budget=3)
    for _ in range(3):
        assert pacer.before_call("m") == 0.0
    assert clock.slept == [], "under budget must be free"
    assert pacer.slept_s == 0.0


def test_the_window_is_full_then_the_next_call_sleeps():
    pacer, clock = _pacer(budget=3)
    for _ in range(3):
        pacer.before_call("m")
    waited = pacer.before_call("m")
    assert waited > 0, "the 4th call in a 3-call budget must be paced"
    assert pacer.sleeps == 1


def test_sleep_is_only_as_long_as_the_oldest_call_needs_to_age_out():
    """Over-sleeping wastes gate time; under-sleeping re-triggers the 429."""
    pacer, clock = _pacer(budget=2)
    pacer.before_call("m")          # t=1000
    clock.advance(10)
    pacer.before_call("m")          # t=1010
    clock.advance(5)                # t=1015, oldest is 15s old
    waited = pacer.before_call("m")
    # The oldest call ages out 60s after it happened, i.e. 45s from now.
    assert waited == pytest.approx(45.0), f"expected ~45s, slept {waited}"


def test_calls_resume_free_once_the_window_has_rolled_past():
    pacer, clock = _pacer(budget=2)
    pacer.before_call("m")
    pacer.before_call("m")
    clock.advance(WINDOW_S + 1)      # everything aged out
    assert pacer.before_call("m") == 0.0


def test_each_model_has_its_own_independent_budget():
    """The fallback model has a SEPARATE quota bucket (measured, slice 44), so
    exhausting the primary must not throttle it."""
    pacer, clock = _pacer(budget=2)
    pacer.before_call("primary")
    pacer.before_call("primary")
    assert pacer.before_call("fallback") == 0.0, \
        "a different model must not inherit the primary's exhausted window"
    assert pacer.before_call("primary") > 0, "the primary IS exhausted"


def test_a_model_never_seen_before_just_gets_its_own_budget():
    pacer, _ = _pacer(budget=1)
    assert pacer.before_call("brand-new-model-name") == 0.0


# ---------------------------------------------------------------- accounting
def test_a_run_with_no_api_calls_sleeps_zero_seconds():
    """DoD clause 4: deterministic-only runs must cost nothing."""
    pacer, clock = _pacer()
    assert pacer.slept_s == 0.0
    assert sum(pacer.counts.values()) == 0
    assert "0 Gemini calls" in pacer.report()
    assert clock.slept == []


def test_counters_report_calls_per_model():
    pacer, _ = _pacer(budget=99)
    for _ in range(3):
        pacer.before_call("a")
    pacer.before_call("b")
    assert pacer.counts == {"a": 3, "b": 1}
    report = pacer.report()
    assert "a=3" in report and "b=1" in report
    assert "4 Gemini calls" in report


def test_the_report_states_the_cost_separately_from_the_count():
    """Risk 1: pacing must never hide slowness — slept time is its own number."""
    pacer, _ = _pacer(budget=1)
    pacer.before_call("m")
    pacer.before_call("m")          # forces one sleep
    report = pacer.report()
    assert "slept" in report and "waits" in report
    assert pacer.slept_s > 0


# ---------------------------------------------------------------- hostile
def test_fifty_calls_in_a_row_are_paced_not_hung():
    pacer, clock = _pacer(budget=5)
    for _ in range(50):
        pacer.before_call("m")
    assert pacer.counts["m"] == 50
    # 50 calls at 5/min needs at least 9 windows of waiting; bounded, finite.
    assert pacer.slept_s > 0
    assert pacer.slept_s < 50 * WINDOW_S, "must not sleep unboundedly"


def test_a_clock_that_jumps_backwards_never_sleeps_negative():
    clock = FakeClock()
    pacer = QuotaPacer(2, clock=clock.now, sleeper=clock.sleep)
    pacer.before_call("m")
    pacer.before_call("m")
    clock.t -= 30          # hostile: time went backwards
    pacer.before_call("m")  # FakeClock.sleep asserts non-negative
    assert all(s >= 0 for s in clock.slept)


def test_count_only_mode_records_but_never_sleeps():
    """Stage 0 needs the call count WITHOUT changing the timing it measures."""
    clock = FakeClock()
    pacer = QuotaPacer(1, clock=clock.now, sleeper=clock.sleep, count_only=True)
    for _ in range(10):
        assert pacer.before_call("m") == 0.0
    assert pacer.counts["m"] == 10
    assert pacer.slept_s == 0.0
    assert clock.slept == []
    assert "COUNT-ONLY" in pacer.report()


# ---------------------------------------------------------------- installation
def test_the_patch_target_exists_on_the_installed_sdk():
    """Risk 5: if the SDK moves this, pacing silently stops and the false
    failures come back. Pin the attribute so we find out here instead."""
    from google.genai.models import Models
    assert hasattr(Models, "generate_content")


def test_install_wraps_the_sdk_and_is_idempotent():
    from google.genai.models import Models
    uninstall()
    original = Models.generate_content
    try:
        first = install()
        assert first is not None
        wrapped = Models.generate_content
        assert wrapped is not original, "install() must actually wrap"
        second = install()
        assert second is first, "installing twice must not double-wrap"
        assert Models.generate_content is wrapped
    finally:
        uninstall()
    assert Models.generate_content is original, "uninstall must restore"


def test_the_escape_hatch_disables_pacing_entirely(monkeypatch):
    """Deliberate-429 work (harness_brain_chain.py) needs a way out."""
    from google.genai.models import Models
    uninstall()
    original = Models.generate_content
    monkeypatch.setenv(ENV_DISABLE, "1")
    try:
        assert install() is None, "the escape hatch must prevent installation"
        assert Models.generate_content is original, "nothing may be wrapped"
    finally:
        uninstall()


def test_the_budget_is_configurable_by_env(monkeypatch):
    monkeypatch.setenv(ENV_BUDGET, "7")
    assert budget_from_env() == 7
    monkeypatch.setenv(ENV_BUDGET, "not-a-number")
    assert budget_from_env() == DEFAULT_BUDGET_PER_MIN, "garbage falls back"


def test_rearm_restores_pacing_after_a_test_tears_it_off():
    """The backstop for the leak that actually happened. If a test uninstalls
    the wrapper, rearm() must put it back WITHOUT losing the call counts, so the
    rest of the session stays paced and the cost report stays honest."""
    from tests import _pacer as pacer_mod
    pacer = QuotaPacer(12)
    pacer.counts["m"] = 7           # pretend it already accounted for calls
    uninstall()
    assert not pacer_mod.is_attached(), "precondition: nothing wrapped"
    try:
        assert pacer_mod.rearm(pacer) is True, "must report that it re-armed"
        assert pacer_mod.is_attached(), "the wrapper must be back on"
        assert pacer.counts["m"] == 7, "re-arming must NOT reset the counters"
        assert pacer_mod.rearm(pacer) is False, "already armed = nothing to do"
    finally:
        uninstall()


def test_is_attached_reports_the_truth_both_ways():
    from tests import _pacer as pacer_mod
    uninstall()
    try:
        assert pacer_mod.is_attached() is False
        install()
        assert pacer_mod.is_attached() is True
    finally:
        uninstall()
    assert pacer_mod.is_attached() is False


def test_a_paced_call_still_reaches_the_real_method():
    """Pacing must not swallow the call — it delays, never replaces."""
    from google.genai.models import Models
    uninstall()
    original = Models.generate_content
    seen = {}

    def fake(self, *, model, **kwargs):
        seen["model"] = model
        seen["kwargs"] = kwargs
        return "real-response"

    Models.generate_content = fake
    try:
        import tests._pacer as pacer_mod
        pacer_mod._original = None
        pacer_mod._installed = None
        pacer = install()
        assert pacer is not None
        result = Models.generate_content(object(), model="m", contents="hi")
        assert result == "real-response", "the underlying call must still run"
        assert seen["model"] == "m"
        assert seen["kwargs"]["contents"] == "hi", "kwargs must pass through"
        assert pacer.counts["m"] == 1, "and the call must be counted"
    finally:
        uninstall()
        Models.generate_content = original
