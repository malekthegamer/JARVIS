"""Slice 6 — the chain tracker: one per think() call, the ground truth of a
multi-step run. It broadcasts plan/step/chain_end events through the state
broadcaster (same lock, same seq stream) so the HUD sees one ordered feed,
and it enriches the EXECUTING detail with a step counter.

Two layers of truth, deliberately separate:
- `calls` — every tool call that actually ran (n, tool, status). Ground truth.
- `steps` — the model's DECLARED plan (descriptive strings, Stage 2). Display
  only; the cursor that walks it is a heuristic (action tools advance it,
  perception tools don't) and is never trusted for anything but rendering.

Lifecycle: brain.think() calls start() and MUST clear() in its finally — a
crashed chain still emits a terminal chain_end, so the HUD can never be left
showing a stale plan strip (state-leak risk from the plan's risk register).

Module-level `_current` is safe because interactions are serialized (server
_busy lock + brain RLock): one chain at a time by construction.
"""
from __future__ import annotations

from jarvis.state import broadcaster

# Tools that observe rather than act — they never advance the declared-step
# cursor. Anything not listed advances on success (new verbs default to
# "action", which at worst advances the display early — never hides a step).
PERCEPTION_TOOLS = frozenset({"read_ui_tree", "plan_steps"})

# A tool result is a hard failure iff the wrapper said so explicitly.
_FAIL_PREFIX = "FAILED"
_CANCEL_PREFIX = "CANCELLED"


def status_from_result(result: str) -> str:
    """Map a tool-result string to a step status. Only the executor's own
    prefixes count — model prose never decides (ground truth rule)."""
    text = (result or "").lstrip()
    if text.startswith(_FAIL_PREFIX):
        return "failed"
    if text.startswith(_CANCEL_PREFIX):
        return "cancelled"
    return "ok"


class ChainTracker:
    def __init__(self) -> None:
        self.steps: list[str] = []          # declared plan (descriptive)
        self.revision = 0                   # bumped per plan_steps call
        self.calls: list[dict] = []         # ground truth: executed tool calls
        self.cursor = 0                     # completed declared steps (display)
        self.failures = 0                   # hard FAILED results (Stage 3 budget)
        self.aborted: str | None = None     # None | reason (Stage 3)

    # ---------- declared plan ----------
    def set_plan(self, steps: list[str]) -> None:
        self.steps = [str(s).strip() for s in steps if str(s).strip()]
        self.revision += 1
        broadcaster.emit({"type": "plan", "steps": self.steps,
                          "revision": self.revision})

    # ---------- ground-truth call tracking ----------
    def begin_call(self, tool: str) -> int:
        n = len(self.calls) + 1
        self.calls.append({"n": n, "tool": tool, "status": "start"})
        broadcaster.emit(self._step_event(n))
        return n

    def end_call(self, n: int, status: str) -> None:
        call = self.calls[n - 1]
        call["status"] = status
        if status == "failed":
            self.failures += 1
        elif status == "ok" and call["tool"] not in PERCEPTION_TOOLS:
            # display heuristic: a successful action completes a declared step
            self.cursor = min(self.cursor + 1, len(self.steps)) \
                if self.steps else self.cursor + 1
        broadcaster.emit(self._step_event(n))

    def _step_event(self, n: int) -> dict:
        call = self.calls[n - 1]
        return {"type": "step", "n": n, "tool": call["tool"],
                "status": call["status"],
                "step": min(self.cursor + 1, len(self.steps)) if self.steps else None,
                "total": len(self.steps) or None}

    # ---------- rendering ----------
    def detail(self, tool: str) -> str:
        """EXECUTING detail: 'k/N · tool' with a declared plan, 'n · tool'
        without one (n = this call's position)."""
        if self.steps:
            k = min(self.cursor + 1, len(self.steps))
            return f"{k}/{len(self.steps)} · {tool}"
        return f"{len(self.calls)} · {tool}"

    def snapshot(self) -> dict:
        """For WS connect replay (Stage 4): enough to re-render the strip."""
        return {"type": "chain", "steps": self.steps, "revision": self.revision,
                "cursor": self.cursor,
                "calls": [dict(c) for c in self.calls],
                "aborted": self.aborted}


_current: ChainTracker | None = None


def start() -> ChainTracker:
    global _current
    _current = ChainTracker()
    return _current


def current() -> ChainTracker | None:
    return _current


def clear(status: str) -> None:
    """End the chain: emit the terminal event and drop the tracker. Always
    called from think()'s finally — even a crash lands here. Pure
    conversation (no plan, no calls) ends silently — nothing to clear."""
    global _current
    tracker = _current
    if tracker is None:
        return
    _current = None
    if tracker.calls or tracker.steps:
        broadcaster.emit({"type": "chain_end", "status": status})
