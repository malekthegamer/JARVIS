"""Slice 57 stage 1 — the opt-in latency recorder.

There is currently NO mouth-to-ear number anywhere in this project:
harness_latency_eval.py instruments model/execute/UIA/vision but not STT, wake,
or the _respond funnel. So "it feels faster" could never be more than a claim.

This module is deliberately OFF unless JARVIS_VOICE_TIMING=1, because it is
meant to be switched on in REAL use on the owner's machine — not only inside a
harness. A profiler that only works in a lab measures the lab.
"""
from __future__ import annotations

import pytest

from jarvis.core import timing


@pytest.fixture(autouse=True)
def _clean_timing(monkeypatch):
    """LEAK GUARD, written with the install (this repo has shipped that bug five
    times). Records are process-wide and the env var is process-wide."""
    monkeypatch.delenv(timing.ENV, raising=False)
    timing.reset()
    yield
    timing.reset()


def test_spans_are_a_noop_without_the_env_var():
    """Zero cost and zero state in normal operation — the recorder must never be
    something the product pays for when nobody asked for it."""
    assert timing.enabled() is False
    with timing.span("brain"):
        pass
    timing.mark("first_audio")
    assert timing.records() == []


def test_spans_record_a_duration_when_enabled(monkeypatch):
    monkeypatch.setenv(timing.ENV, "1")
    clock = iter([10.0, 10.25])
    monkeypatch.setattr(timing.time, "monotonic", lambda: next(clock))

    with timing.span("stt"):
        pass

    (rec,) = timing.records()
    assert rec["name"] == "stt"
    assert rec["ms"] == pytest.approx(250.0)


def test_a_span_records_even_when_the_body_raises(monkeypatch):
    """A failing brain round is exactly when you most want the number. Recording
    in `finally` also means the span cannot swallow the exception."""
    monkeypatch.setenv(timing.ENV, "1")

    with pytest.raises(ValueError):
        with timing.span("brain"):
            raise ValueError("boom")

    (rec,) = timing.records()
    assert rec["name"] == "brain" and rec["ms"] is not None


def test_mark_records_an_instant_not_a_duration(monkeypatch):
    """time-to-first-audio is a POINT in time, not an elapsed span — the moment
    pygame starts playing. It needs a different shape from span()."""
    monkeypatch.setenv(timing.ENV, "1")
    timing.mark("first_audio")

    (rec,) = timing.records()
    assert rec["name"] == "first_audio"
    assert rec["ms"] is None
    assert isinstance(rec["t"], float)


def test_records_are_ordered_and_reset_clears_them(monkeypatch):
    monkeypatch.setenv(timing.ENV, "1")
    timing.mark("a")
    with timing.span("b"):
        pass
    timing.mark("c")

    assert [r["name"] for r in timing.records()] == ["a", "b", "c"]
    timing.reset()
    assert timing.records() == []


def test_elapsed_between_two_marks(monkeypatch):
    """The harness reports mouth-to-ear as the gap between two marks, so that
    arithmetic belongs here where it can be tested, not in the harness."""
    monkeypatch.setenv(timing.ENV, "1")
    clock = iter([100.0, 101.5])
    monkeypatch.setattr(timing.time, "monotonic", lambda: next(clock))
    timing.mark("speech_end")
    timing.mark("first_audio")

    assert timing.elapsed_ms("speech_end", "first_audio") == pytest.approx(1500.0)


def test_elapsed_is_none_when_a_mark_is_missing(monkeypatch):
    """Never invent a number. A missing mark means the path did not run — say
    so rather than reporting a plausible-looking zero."""
    monkeypatch.setenv(timing.ENV, "1")
    timing.mark("speech_end")
    assert timing.elapsed_ms("speech_end", "first_audio") is None


def test_report_names_every_span_and_never_raises(monkeypatch):
    monkeypatch.setenv(timing.ENV, "1")
    with timing.span("stt"):
        pass
    timing.mark("first_audio")

    out = timing.report()
    assert "stt" in out and "first_audio" in out
    timing.reset()
    assert isinstance(timing.report(), str)      # empty case still fine
