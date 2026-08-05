"""Slice 63 — live proof that launch_app really starts an elevated program.

The unit tests in tests/test_launch_elevation.py fake Popen and startfile, so
they prove the BRANCHING is right but never touch Windows. This drives the real
apps.launch_app() against a real requires-elevation target.

It lives in a harness, not the gate, on purpose: it puts a UAC consent prompt on
the user's screen, and a test suite that prompts on every run is a test suite
people stop running.

    python tests/harness_elevated_launch.py            # regedit stand-in
    python tests/harness_elevated_launch.py "forza horizon 6"

Either answer to the prompt is a useful result:
    approve -> ok=True,  "Launched ... as administrator."
    decline -> ok=False, "... the Windows prompt was declined ..."
Both prove ShellExecute was reached; the old code could only ever produce
"[WinError 740] The requested operation requires elevation".
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import _harness_env  # noqa: E402,F401  (audit isolation: import BEFORE jarvis)

from jarvis.primitives import apps  # noqa: E402


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "regedit"
    target, matched = apps.resolve_app(name)
    print(f"resolve_app({name!r}) -> {target!r}  (matched {matched!r})")
    if not target:
        print("FAIL  nothing resolved; nothing to prove")
        return 2

    print("\nA UAC prompt will appear. Either answer proves the fix.")
    t0 = time.time()
    result = apps.launch_app(name)
    took = time.time() - t0

    print(f"\n  ok       : {result['ok']}")
    print(f"  pid      : {result['pid']}")
    print(f"  resolved : {result['resolved']}")
    print(f"  message  : {result['message']}")
    print(f"  took     : {took:.1f}s")

    msg = result["message"].lower()
    if "requires elevation" in msg or "winerror 740" in msg:
        print("\nFAIL  still dying at CreateProcess — the fallback did not run")
        return 1
    if result["ok"]:
        print("\nPASS  launched through ShellExecute (elevated)")
    elif "declined" in msg:
        print("\nPASS  reached ShellExecute; the prompt was declined, reported honestly")
    elif "still showing" in msg:
        print("\nPASS  reached ShellExecute; prompt unanswered, bounded and reported")
    else:
        print("\nFAIL  unexpected failure — read the message above")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
