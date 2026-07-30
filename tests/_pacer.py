"""Test-only Gemini call pacer — stops free-tier RPM limits forging failures.

WHY THIS EXISTS (slice 45). For seven slices every gate run carried 6-9 failures
that were never bugs: free-tier Gemini rate limits. That is not an inconvenience,
it is lost signal — gate 43 produced a failure indistinguishable from a
regression I had just caused in the same slice.

Slice 44 tried to fix it from brain.py with a model fallback chain and the
measurement refused the claim (6 -> 7 failures; chain engaged 9x, rescued 3),
because the suite bursts past BOTH models' combined ~30 RPM. The conclusion it
proved: this is a TEST-PACING problem, not a brain problem.

MEASURED facts this rests on:
  * the primary caps at ~15 RPM (429 at burst 15-17)
  * a 429 needs ~22s to clear (still 429 at 18s) — too long to wait in the
    product, which is why the product-side backoff was deliberately NOT built

Design: a per-model sliding window. A call is allowed if that model has made
fewer than `budget` calls in the last 60s; otherwise we sleep exactly long enough
for the oldest call to age out. Deterministic tests make no API calls, so they
never sleep — the cost is paid only where the quota is actually consumed.

This is TEST INFRASTRUCTURE. It never ships in the product and never changes
product behaviour; it only slows the test process down enough to stay legal.
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque

# Measured cap is ~15 RPM; 12 leaves deliberate headroom for the retry the
# chain may still make and for clock skew against Google's own window.
DEFAULT_BUDGET_PER_MIN = 12
WINDOW_S = 60.0

ENV_DISABLE = "JARVIS_TEST_NO_PACING"
ENV_BUDGET = "JARVIS_TEST_RPM_BUDGET"
ENV_COUNT_ONLY = "JARVIS_TEST_COUNT_ONLY"


class QuotaPacer:
    """Per-model sliding-window rate limiter with call accounting.

    `sleeper` and `clock` are injected so the tests can drive it with a fake
    clock and assert the sleep DURATIONS without ever really waiting.
    """

    def __init__(self, budget_per_min: int = DEFAULT_BUDGET_PER_MIN,
                 clock=time.monotonic, sleeper=time.sleep,
                 count_only: bool = False) -> None:
        self.budget = max(1, int(budget_per_min))
        self.clock = clock
        self.sleeper = sleeper
        self.count_only = count_only
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        # Accounting — reported at session end so the COST is never hidden.
        self.counts: dict[str, int] = defaultdict(int)
        self.slept_s: float = 0.0
        self.sleeps: int = 0

    # ---------- the pacing decision ----------
    def _wait_for(self, model: str) -> float:
        """Seconds to sleep so `model` stays under budget. 0 = go now.

        Uses a monotonic clock, so a wall-clock jump cannot produce a negative
        wait; the max(0.0, ...) is a second belt on that.
        """
        window = self._calls[model]
        now = self.clock()
        while window and now - window[0] >= WINDOW_S:
            window.popleft()
        if len(window) < self.budget:
            return 0.0
        return max(0.0, WINDOW_S - (now - window[0]))

    def before_call(self, model: str) -> float:
        """Block until `model` may be called. Returns the seconds slept."""
        with self._lock:
            self.counts[model] += 1
            if self.count_only:
                self._calls[model].append(self.clock())
                return 0.0
            waited = 0.0
            # A loop, not a single sleep: after waking, other entries may also
            # have aged out (or not), so re-decide rather than assume.
            while True:
                wait = self._wait_for(model)
                if wait <= 0:
                    break
                self.sleeper(wait)
                self.slept_s += wait
                self.sleeps += 1
                waited += wait
            self._calls[model].append(self.clock())
            return waited

    # ---------- reporting ----------
    def report(self) -> str:
        total = sum(self.counts.values())
        if not total:
            return "quota pacer: 0 Gemini calls — nothing paced (0.0s slept)"
        per_model = ", ".join(f"{m}={n}" for m, n in sorted(self.counts.items()))
        mode = "COUNT-ONLY (not pacing)" if self.count_only else f"budget {self.budget}/min"
        return (f"quota pacer [{mode}]: {total} Gemini calls ({per_model}); "
                f"slept {self.slept_s:.1f}s across {self.sleeps} waits")


# ---------- installation into the SDK ----------
_installed: QuotaPacer | None = None
_original = None


def budget_from_env() -> int:
    try:
        return max(1, int(os.environ.get(ENV_BUDGET, DEFAULT_BUDGET_PER_MIN)))
    except ValueError:
        return DEFAULT_BUDGET_PER_MIN


def is_attached() -> bool:
    """Is the pacing wrapper currently on the SDK method?"""
    from google.genai.models import Models
    return getattr(Models.generate_content, "_jarvis_paced", False)


def _attach(pacer: QuotaPacer) -> None:
    """Put the wrapper on the SDK method for `pacer`, preserving its counters.

    The `_jarvis_paced` marker makes this idempotent and lets `is_attached()`
    tell "wrapped" from "not wrapped" without guessing.
    """
    global _installed, _original
    from google.genai.models import Models
    if is_attached():
        _installed = pacer          # already wrapped; just own it
        return
    original = Models.generate_content

    def paced(self, *, model, **kwargs):
        pacer.before_call(str(model))
        return original(self, model=model, **kwargs)

    paced._jarvis_paced = True
    Models.generate_content = paced
    _original = original
    _installed = pacer


def install() -> QuotaPacer | None:
    """Wrap the ONE SDK method every Gemini call goes through.

    All three call sites (gemini_provider.py:84, vision.py:232 and :335) end up
    in google.genai.models.Models.generate_content, so patching the class covers
    brain AND both vision paths regardless of which client instance is used.

    Fails LOUDLY if the SDK moves that attribute: silently not pacing would
    quietly restore the exact false-failure problem this slice exists to kill.
    Idempotent — installing twice does not double-wrap.
    """
    global _installed
    if os.environ.get(ENV_DISABLE):
        return None
    if _installed is not None:
        return _installed

    from google.genai.models import Models
    if not hasattr(Models, "generate_content"):   # risk 5, made loud
        raise RuntimeError(
            "google.genai.models.Models.generate_content is gone — the test "
            "quota pacer cannot install, and without it free-tier rate limits "
            "will forge test failures again. Fix the patch target.")

    pacer = QuotaPacer(budget_from_env(),
                       count_only=bool(os.environ.get(ENV_COUNT_ONLY)))
    _attach(pacer)
    return pacer


def rearm(pacer: QuotaPacer) -> bool:
    """Re-attach `pacer` if something tore the wrapper off. True if re-armed.

    Exists because a test DID tear it off once (tests/test_quota_pacer.py, found
    in the stage-2 gate) and every later test in that session ran unpaced and
    uncounted. Counters are preserved, so the cost report stays honest.
    """
    if os.environ.get(ENV_DISABLE) or is_attached():
        return False
    _attach(pacer)
    return True


def uninstall() -> None:
    """Restore the real method (used by the pacer's own tests)."""
    global _installed, _original
    if _original is not None:
        from google.genai.models import Models
        Models.generate_content = _original
        _original = None
    _installed = None


def current() -> QuotaPacer | None:
    return _installed
