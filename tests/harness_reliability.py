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


# ---------------------------------------------------------------- slice 67
# This report chose the work for slices 60-66. It was wrong in three ways, and
# each one cost real planning time:
#   * a BLOCKED action (kill switch, denylisted path) scored as breakage — that
#     is the safety system working exactly as designed;
#   * a declined UAC prompt scored as breakage — that is a person saying no;
#   * no date window, so browse_fill's "78% worst verb" and click's "35%" were
#     quoted for weeks after both stopped happening. Two slices were started on
#     those numbers and abandoned once the dates were checked.
# Hence: three buckets, a default window, and dates printed on every row.

DEFAULT_WINDOW_DAYS = 30


def buckets(rows) -> dict:
    """broke / refused / declined / ok — only `broke` is a defect."""
    out = {"broke": 0, "refused": 0, "declined": 0, "ok": 0}
    for r in rows:
        st = r.get("status")
        if st == "failed":
            out["broke"] += 1
        elif st == "refused":
            out["refused"] += 1
        elif st == "cancelled":
            out["declined"] += 1
        else:
            out["ok"] += 1
    return out


def within_days(rows, days: int | None, now: str | None = None):
    """Rows whose ts is within `days` of now. days=None means all history."""
    if not days:
        return list(rows)
    from datetime import datetime, timedelta, timezone
    if now:
        ref = datetime.fromisoformat(str(now))
    else:
        ref = datetime.now(timezone.utc)
    cutoff = ref - timedelta(days=days)
    kept = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(str(r.get("ts")))
        except Exception:
            continue          # undateable row cannot be trusted in a window
        if ts >= cutoff:
            kept.append(r)
    return kept


def _dates(rows) -> str:
    ds = sorted({str(r.get("ts"))[:10] for r in rows if r.get("ts")})
    if not ds:
        return "?"
    return ds[0] if len(ds) == 1 else f"{ds[0]}..{ds[-1]}"


def _rows(recent: int | None):
    rows = audit_log.read_envelopes()
    return rows[-recent:] if recent else rows


def report(recent: int | None, days: int | None = DEFAULT_WINDOW_DAYS) -> None:
    rows = within_days(_rows(recent), days)
    if not rows:
        span = "ever" if not days else f"in the last {days} days"
        print(f"no audited actions {span} — use JARVIS, then re-run this")
        return
    total = len(rows)
    b = buckets(rows)
    broke = [r for r in rows if r.get("status") == "failed"]

    window = "all history" if not days else f"last {days} days"
    print(f"\n{'='*66}")
    print(f"RELIABILITY over {total} real actions ({window}, {_dates(rows)})"
          + (f" [most recent {recent}]" if recent else ""))
    print("=" * 66)
    print(f"  BROKE     : {b['broke']:4d}   {100*b['broke']/total:5.1f}%  "
          f"<- the only number that is a defect")
    print(f"  refused   : {b['refused']:4d}   {100*b['refused']/total:5.1f}%  "
          f"(a kill switch or denylist said no — working as designed)")
    print(f"  declined  : {b['declined']:4d}   {100*b['declined']/total:5.1f}%  "
          f"(the USER said no — a choice, not a fault)")
    print(f"  ok        : {b['ok']:4d}")
    print()
    print(f"  {'tool':22s} {'broke':>5s} {'used':>5s}  rate   when")
    print(f"  {'-'*22} {'-'*5} {'-'*5}  ----   ----")
    used = Counter(r.get("tool") or "?" for r in rows)
    bad = Counter(r.get("tool") or "?" for r in broke)
    for tool, n in sorted(bad.items(), key=lambda t: -t[1]):
        rate = 100 * n / max(used[tool], 1)
        flag = "  <-- worst" if rate >= 50 else ""
        when = _dates([r for r in broke if (r.get("tool") or "?") == tool])
        print(f"  {tool:22s} {n:5d} {used[tool]:5d}  {rate:3.0f}%   {when}{flag}")
    clean = [t for t in used if not bad[t]]
    if clean:
        print(f"\n  never broke: {', '.join(sorted(clean))}")

    print("\n  Read the WHEN column before acting on a rate. Twice now a slice")
    print("  was planned on a number whose failures had all stopped weeks")
    print("  earlier (browse_fill 78%, click 35%). Use --days 0 for all")
    print("  history, and remember entries before 2026-08-05 also include")
    print("  harness runs that polluted this log (fixed in slice 62).")


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
    ap.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS,
                    help="window in days; 0 = all history")
    args = ap.parse_args()
    if args.why:
        why(args.why, args.recent)
    else:
        report(args.recent, args.days or None)
