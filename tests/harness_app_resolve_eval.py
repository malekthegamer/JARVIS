"""Slice 64 — score app-name resolution against the REAL installed inventory.

The owner: "it can't open most games/apps on my desktop." Slice 63 fixed
LAUNCHING (Popen can't elevate). This measures the other half: whether the name
a person actually says resolves to the right program at all.

Deliberately a harness, not a gate test — the expected answers depend on what is
installed on THIS machine, and a test that encodes one person's Steam library is
a test that fails for everyone else. The deterministic pattern matrix lives in
tests/test_app_resolution.py with a fake inventory.

    python tests/harness_app_resolve_eval.py
    python tests/harness_app_resolve_eval.py --list      # dump the inventory

Three outcomes, and only one of them is a bug:
    UNIQUE  resolved to exactly one thing  -> must be the RIGHT thing
    ASK     several genuine matches        -> correct behaviour, not a failure
    MISS    nothing                        -> correct ONLY if not installed
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import _harness_env  # noqa: E402,F401  (audit isolation: import BEFORE jarvis)

from jarvis.primitives import app_discovery, apps  # noqa: E402

# (phrase, expectation) — expectation is "unique:<substring the answer must
# contain>", "ask", or "miss". Written from what is genuinely installed here;
# re-derive it with --list on a different machine.
PROBES: list[tuple[str, str]] = [
    # --- should resolve to exactly one thing -------------------------------
    ("prism launcher",      "unique:prism"),
    ("prismlauncher",       "unique:prism"),        # no space
    ("spider-man 2",        "unique:spider"),       # 'Spider-Man2' — digit stuck on
    ("spiderman 2",         "unique:spider"),       # no hyphen either
    ("fifa",                "unique:fifa"),         # vs 'FIFA 22 Settings'
    ("resident evil 4",     "unique:resident evil 4"),
    ("need for speed heat", "unique:heat"),
    ("geometry dash",       "unique:geometry dash"),
    ("escape the backrooms", "unique:backrooms"),
    ("steam",               "unique:steam"),
    ("discord",             "unique:discord"),
    ("chrome",              "unique:chrome"),
    ("blender",             "unique:blender"),
    ("capcut",              "unique:capcut"),
    ("vs code",             "unique:code"),         # via apps.py's fast ladder
    ("notepad",             "unique:notepad"),      # via APP_ALIASES
    # --- genuinely ambiguous: asking is the RIGHT answer -------------------
    ("resident evil",       "ask"),                 # RE2 / RE3 / RE4
    ("need for speed",      "ask"),                 # Heat / Most Wanted
    ("black ops 2",         "ask"),                 # base / multiplayer / zombies
    # --- honestly absent ---------------------------------------------------
    ("obs",                 "miss"),                # not installed here
]


def outcome(phrase: str) -> tuple[str, str]:
    """(kind, detail) for one phrase, using the real resolution path."""
    try:
        target, matched = apps.resolve_app(phrase)
    except Exception as exc:
        return "ERROR", f"{type(exc).__name__}: {exc}"
    if target:
        return "UNIQUE", str(matched or target)
    try:
        hit = app_discovery.find(phrase)
    except Exception:
        hit = None
    if isinstance(hit, dict) and hit.get("candidates"):
        return "ASK", ", ".join(hit["candidates"][:4])
    near = app_discovery.suggest(phrase)
    if near:
        # Suggestions exist but resolution refused — from the user's seat this
        # is still a failed request, so it does NOT count as a clean ASK unless
        # resolution itself surfaced the choice.
        return "MISS", f"(suggested: {', '.join(near[:3])})"
    return "MISS", ""


def main() -> int:
    if "--list" in sys.argv:
        entries = app_discovery.desktop_shortcuts() + \
            app_discovery.steam_games() + app_discovery.epic_games()
        for e in sorted(entries, key=lambda x: str(x.get("name", ""))):
            print(f"  {e.get('source','?'):8s} {e.get('name','')}")
        print(f"\n{len(entries)} entries")
        return 0

    good = bad = 0
    print(f"\n{'phrase':22s} {'got':7s} {'want':7s}  detail")
    print("-" * 78)
    for phrase, expect in PROBES:
        kind, detail = outcome(phrase)
        want = expect.split(":", 1)[0].upper()
        if kind == "UNIQUE" and want == "UNIQUE":
            needle = expect.split(":", 1)[1].casefold()
            ok = needle in detail.casefold()
            if not ok:
                detail = f"WRONG APP -> {detail} (wanted {needle!r})"
        else:
            ok = (kind == want)
        good, bad = (good + 1, bad) if ok else (good, bad + 1)
        print(f"{phrase:22s} {kind:7s} {want:7s}  {'ok ' if ok else 'FAIL'} {detail[:40]}")

    total = len(PROBES)
    print("-" * 78)
    print(f"  {good}/{total} correct, {bad} wrong")
    print("  Definition of Done (slice 64): >= 16/20, and ZERO 'WRONG APP' rows.")
    return 0 if good >= 16 else 1


if __name__ == "__main__":
    raise SystemExit(main())
