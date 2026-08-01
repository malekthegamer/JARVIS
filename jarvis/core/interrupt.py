"""Barge-in (slice 49) — the ONE cancel path, shared by every trigger.

Cutting JARVIS off is two separate things, and it must do both:

  1. STOP TALKING   -- playback.stop() sets a flag the play loop checks, so the
                       current utterance ends mid-sentence.
  2. RUN NO MORE    -- mark the live chain aborted, which makes
                       ChainTracker.pre_call_guard refuse every remaining tool
                       call with an honest message. That mechanism already
                       existed for declined confirmations and failure budgets;
                       barge-in reuses it rather than inventing a second one.

WHAT IT DELIBERATELY DOES NOT DO:

  * It never touches server._busy. A stop that waited on the lock held by the
    very interaction it is cancelling would deadlock forever. Setting a flag and
    stopping audio needs no lock, which is exactly why this works mid-chain.
  * It cannot un-fire a step already in flight. A click that has fired has
    fired. "Stop" prevents the NEXT step -- the same honest boundary the
    existing aborted path has always had, stated rather than glossed over.
  * It never starts a new interaction. The wake trigger stops and returns; the
    user speaks again normally once things are idle. Anything else would
    re-enter _busy and stack triggers, which is the invariant _busy exists for.

Never raises: an audio failure (no speakers, wedged mixer) must not prevent the
chain from aborting, which is the more important half.
"""
from __future__ import annotations

import threading

from jarvis.core import chain
from jarvis.voice import playback

REASON = "interrupted"

# Slice 57. Barge-in must end the CONVERSATION, not merely the sentence.
# Once a follow-up window exists, stopping JARVIS mid-reply and then having him
# immediately open a listening window reads as being ignored. A module-level
# Event is used rather than a lock so `request()` stays lock-free and can never
# block on the interaction it is cancelling — the invariant this module is built
# around.
_conversation_stop = threading.Event()


def begin_conversation() -> None:
    """Called when a conversation starts, so a stale stop cannot kill it."""
    _conversation_stop.clear()


def conversation_stopped() -> bool:
    return _conversation_stop.is_set()


def request() -> bool:
    """Stop speaking and stop the chain. True if anything was actually running.

    False is a normal answer, not an error: pressing stop when JARVIS is idle,
    or twice in a row, is a harmless no-op.
    """
    # Audio first -- it is what the user hears, so it should stop with the least
    # possible delay, and it must happen even if there is no chain at all (the
    # commonest case is JARVIS reading out a long answer with no tools running).
    interrupted = False
    # Set FIRST and unconditionally: even when nothing is currently running, the
    # user's "stop" must close any follow-up window that is about to open.
    _conversation_stop.set()
    try:
        playback.stop()
    except Exception:
        pass  # no audio device is not a reason to leave the chain running

    tracker = chain.current()
    if tracker is not None and not tracker.aborted:
        tracker.aborted = REASON
        interrupted = True
    return interrupted
