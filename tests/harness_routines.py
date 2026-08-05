"""Slice 48 live proof — save a routine, then run it BY NAME through the brain.

The unit tests use fake primitives so they are deterministic. This drives the
REAL model and REAL primitives end to end, and checks the two claims that matter:

  1. saying a bare routine name actually runs it (Stage 0 measured the model
     maps "work mode" -> run_routine 4/4 *because* the names are in the prompt)
  2. a CONFIRM step inside a routine STILL PROMPTS -- a routine is stored steps,
     never stored authority

    python tests/harness_routines.py

Uses volume as the observable effect because it is readback-verifiable and
instantly restorable. Restores your volume on exit, including on failure.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import _harness_env  # noqa: E402,F401  (audit isolation: import BEFORE jarvis)
from jarvis import config                                    # noqa: E402
from jarvis.core import routines as R                        # noqa: E402
from jarvis.core.confirmations import confirmations          # noqa: E402
from jarvis.primitives import system                         # noqa: E402

ROUTINE = "harness demo mode"
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    if not config.get_api_key("gemini"):
        print("no GEMINI_API_KEY — cannot prove the model half")
        return 2

    start = system.get_volume()
    if not start["ok"]:
        print("cannot read volume — nothing to verify against")
        return 2
    original = start["level"]
    target = 30 if abs(original - 30) > 5 else 65
    print(f"volume now {original}%; the routine will set it to {target}%\n")

    # A routine with a real, readback-verifiable effect.
    R.routine_store.save(ROUTINE, [{"tool": "set_volume", "args": {"level": target}}])
    print(f"saved routine {ROUTINE!r}")

    prompts: list[str] = []

    def watch(event):
        if event.get("type") == "confirm_request":
            prompts.append(event.get("description", ""))
            threading.Thread(target=lambda: (
                time.sleep(0.05),
                confirmations.resolve(event["id"], True))).start()

    unsub = confirmations.subscribe(watch)
    try:
        from jarvis.brain import JarvisBrain
        brain = JarvisBrain()

        # 1. does the BARE name run it? (the Stage-0 claim, end to end)
        print(f"\nasking the model, verbatim: {ROUTINE!r}")
        reply = brain.think(ROUTINE)
        print(f"  reply: {reply[:160]}")
        after = system.get_volume()
        called = [m["name"] for m in brain.history if m.get("role") == "tool"]
        check("bare_name_ran_the_routine", "run_routine" in called,
              f"tools called: {called}")
        check("the_routine_actually_changed_the_volume",
              after["ok"] and abs(after["level"] - target) <= 2,
              f"volume={after.get('level')} target={target}")

        # 2. does a CONFIRM step inside a routine still prompt?
        R.routine_store.save(ROUTINE, [
            {"tool": "set_volume", "args": {"level": target}},
            {"tool": "run_shell", "args": {"command": "echo routine-confirm-test"}},
        ])
        prompts.clear()
        brain2 = JarvisBrain()
        print(f"\nrunning it again with a shell step (must PROMPT)...")
        brain2.think(f"run my {ROUTINE} routine")
        check("a_confirm_step_inside_a_routine_still_prompts", bool(prompts),
              f"prompts seen: {prompts[:1]}")
    finally:
        unsub()
        R.routine_store.delete(ROUTINE)
        system.set_volume(original)
        back = system.get_volume()
        print(f"\nrestored volume to {back.get('level')}% "
              f"(was {original}%); routine deleted")

    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
