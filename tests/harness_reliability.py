"""Slice 60 — the reliability metric, read from REAL use.

This project's habit is that "better" must be a number (slice 16's vision
scorer, slice 20's latency profile, slice 57's mouth-to-ear harness). Until now
"is JARVIS reliable?" had no such number, so the owner's "it's just so
unreliable" could only be answered with opinion.

It turns out the number already existed. The audit log records every action with
a status, so the failure rate is sitting there waiting to be counted. The first
run of this harness produced the finding that started slice 60:

    313 actions, 51 failed  ->  16% of everything the owner asks for
      browse_fill     7/9   78%
      list_tabs       2/3   67%
      click           7/20  35%
      browse_click    4/12  33%
      browse_navigate 10/42 24%      <- open_url exists because of this row
      run_shell       4/18  22%
      launch_app      7/46  15%      <- suggest() exists because of this row

NOT pytest-collected (harness_ prefix): it reads the owner's real audit log,
so it is a diagnostic, not an assertion. Re-run it after a week of use to see
whether a change actually helped.

    python tests/harness_reliability.py              # all history
    python tests/harness_reliability.py --recent 100 # just the last 100 actions
    python tests/harness_reliability.py --why click  # the actual failure texts
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from jarvis.core.audit import audit_log  # noqa: E402


def _rows(recent: int | None):
    rows = audit_log.read_envelopes()
    return rows[-recent:] if recent else rows


def report(recent: int | None) -> None:
    rows = _rows(recent)
    if not rows:
        print("no audited actions yet — use JARVIS, then re-run this")
        return
    total = len(rows)
    failed = [r for r in rows if r.get("status") == "failed"]
    cancelled = [r for r in rows if r.get("status") == "cancelled"]

    print(f"\n{'='*62}")
    print(f"RELIABILITY over {total} real actions"
          + (f" (most recent {recent})" if recent else " (all history)"))
    print("=" * 62)
    print(f"  failed    : {len(failed):4d}   {100*len(failed)/total:5.1f}%  "
          f"<- the number that matters")
    print(f"  cancelled : {len(cancelled):4d}   {100*len(cancelled)/total:5.1f}%  "
          f"(user declined — not a fault)")
    print(f"  ok        : {total-len(failed)-len(cancelled):4d}")
    print()
    print(f"  {'tool':22s} {'fail':>5s} {'used':>5s}  rate")
    print(f"  {'-'*22} {'-'*5} {'-'*5}  ----")
    used = Counter(r.get("tool") or "?" for r in rows)
    bad = Counter(r.get("tool") or "?" for r in failed)
    for tool, n in sorted(bad.items(), key=lambda t: -t[1]):
        rate = 100 * n / max(used[tool], 1)
        flag = "  <-- worst" if rate >= 50 else ""
        print(f"  {tool:22s} {n:5d} {used[tool]:5d}  {rate:3.0f}%{flag}")
    clean = [t for t in used if not bad[t]]
    if clean:
        print(f"\n  never failed: {', '.join(sorted(clean))}")


def why(tool: str, recent: int | None, limit: int = 8) -> None:
    """The verbatim failure texts — what the MODEL saw, which is what decides
    whether it could recover."""
    rows = audit_log.read_envelopes()
    idxs = [i for i, r in enumerate(rows)
            if r.get("status") == "failed" and r.get("tool") == tool]
    if recent:
        idxs = idxs[-recent:]
    if not idxs:
        print(f"no recorded failures for {tool!r}")
        return
    print(f"\n=== what the model saw when {tool!r} failed ===")
    seen = set()
    for i in idxs[-limit:]:
        try:
            msg = str(audit_log.read_payload(i).get("result", ""))[:220]
        except Exception as exc:
            msg = f"(payload unreadable: {exc})"
        msg = " ".join(msg.split())
        if msg in seen:
            continue
        seen.add(msg)
        print(f"  - {msg}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent", type=int, default=None)
    ap.add_argument("--why", metavar="TOOL", default=None)
    args = ap.parse_args()
    if args.why:
        why(args.why, args.recent)
    else:
        report(args.recent)
