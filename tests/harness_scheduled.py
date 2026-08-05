"""Slice 50 live proof — a real schedule fires, and an unattended CONFIRM PARKS.

The unit tests use a fake clock and fake tools. This drives the real scheduler
tick, the real routine store, and real primitives, and checks the two claims the
slice actually makes:

  1. a due schedule runs its routine, and the effect is REAL (volume readback)
  2. a step needing approval is PARKED -- not run, and NOT prompted at an empty
     room. This is the cardinal rule: an unattended agent must never be able to
     approve itself.

    python tests/harness_scheduled.py

Restores your volume and removes everything it created, including on failure.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import _harness_env  # noqa: E402,F401  (audit isolation: import BEFORE jarvis)
from jarvis import server                                  # noqa: E402
from jarvis.core import schedules as S                     # noqa: E402
from jarvis.core.confirmations import confirmations        # noqa: E402
from jarvis.core.routines import routine_store             # noqa: E402
from jarvis.primitives import system                       # noqa: E402

SAFE = "harness sched safe"
RISKY = "harness sched risky"
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    start = system.get_volume()
    if not start["ok"]:
        print("cannot read volume — nothing to verify against")
        return 2
    original = start["level"]
    target = 30 if abs(original - 30) > 5 else 65
    print(f"volume now {original}%; the scheduled routine will set it to {target}%\n")

    prompts: list[str] = []
    unsub = confirmations.subscribe(
        lambda e: prompts.append(e.get("description", ""))
        if e.get("type") == "confirm_request" else None)

    now = datetime.now().replace(second=0, microsecond=0)
    at = now.strftime("%H:%M")

    try:
        # ---- 1. an all-AUTO routine really runs -------------------------
        routine_store.save(SAFE, [{"tool": "set_volume", "args": {"level": target}}])
        S.schedule_store.add(SAFE, kind="daily", at=at)
        print(f"[1/3] scheduled {SAFE!r} at {at} (now) — ticking the real scheduler")
        server._scheduler_tick(now)

        after = system.get_volume()
        check("the_scheduled_routine_actually_ran",
              after["ok"] and abs(after["level"] - target) <= 2,
              f"volume={after.get('level')} target={target}")

        stamped = [s for s in S.schedule_store.all() if s["routine"] == SAFE]
        check("last_run_was_stamped", bool(stamped) and stamped[0]["last_run"],
              f"last_run={stamped[0]['last_run'] if stamped else None}")

        # ---- 2. it does not fire twice in the same window ---------------
        system.set_volume(original)
        print(f"\n[2/3] ticking again in the same minute — must NOT re-fire")
        server._scheduler_tick(now)
        again = system.get_volume()
        check("did_not_fire_twice_in_the_same_window",
              abs(again["level"] - original) <= 2,
              f"volume={again.get('level')} (restored {original})")

        # ---- 3. THE CARDINAL RULE: a CONFIRM step parks, silently -------
        prompts.clear()
        routine_store.save(RISKY, [
            {"tool": "set_volume", "args": {"level": target}},
            {"tool": "run_shell", "args": {"command": "echo scheduled-park-test"}},
        ])
        S.schedule_store.add(RISKY, kind="daily", at=at)
        print(f"\n[3/3] scheduled {RISKY!r} (contains run_shell) — must PARK it")
        reports: list[str] = []
        real_run = server._run_scheduled

        def capture(rec):
            from jarvis import primitives
            from jarvis.core import chain
            chain.start(unattended=True)
            try:
                reports.append(str(primitives.execute("run_routine",
                                                      {"name": rec["routine"]})))
            finally:
                chain.clear("done")
        server._run_scheduled = capture
        try:
            server._scheduler_tick(now)
        finally:
            server._run_scheduled = real_run

        check("no_confirmation_was_prompted_at_an_empty_room", prompts == [],
              f"prompts={prompts}")
        vol = system.get_volume()
        check("the_auto_step_before_it_still_ran",
              abs(vol["level"] - target) <= 2, f"volume={vol.get('level')}")

        # The check that was MISSING the first time: "no prompt" passed while
        # the run still claimed "all 2 steps completed (set_volume, run_shell)".
        # A parked step must never be reported as done.
        report = reports[0] if reports else ""
        print(f"      report: {report[:150]}")
        check("the_parked_step_is_NOT_reported_as_completed",
              "all 2 steps completed" not in report, report[:110])
        check("the_report_names_what_was_skipped",
              "SKIPPED" in report and "run_shell" in report, report[:110])
    finally:
        unsub()
        for name in (SAFE, RISKY):
            for s in [x for x in S.schedule_store.all() if x["routine"] == name]:
                S.schedule_store.cancel(s["id"])
            routine_store.delete(name)
        system.set_volume(original)
        back = system.get_volume()
        print(f"\nrestored volume to {back.get('level')}%; schedules and "
              f"routines removed")

    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
