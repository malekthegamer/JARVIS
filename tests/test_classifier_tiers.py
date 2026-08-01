"""Slice 55 — deterministic tier tests for the four classifiers that had none.

Tiering IS the safety model: a classifier that returns "auto" where it should
return "confirm" removes the gate silently, and nothing else in the stack will
notice. `classify_type`, `classify_web_key`, `classify_create_shortcut` and
`classify_rename_path` each decide their tier from ARGUMENTS at run time and had
no deterministic coverage, so a refactor could have weakened any of them without
turning a single test red.

No desktop, no network, no model: every external seam is monkeypatched, so this
module stays out of conftest's _DESKTOP_DRIVING_MODULES.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.primitives import fsaccess
from jarvis.primitives import input as jinput
from jarvis.primitives import web

VALID_TIERS = {"auto", "confirm", "blocked"}


# ============================== classify_type ==============================

def _type_into(monkeypatch, title):
    monkeypatch.setattr(jinput, "_target_window", lambda w: (None, title))


def test_type_into_a_normal_window_is_auto(monkeypatch):
    _type_into(monkeypatch, "Untitled - Notepad")
    assert jinput.classify_type({"text": "hello", "window": "Notepad"})["tier"] == "auto"


@pytest.mark.parametrize("title", [
    "Windows PowerShell", "cmd.exe", "Command Prompt", "Windows Terminal",
])
def test_type_into_a_terminal_confirms(monkeypatch, title):
    """A terminal turns typed text into EXECUTION. This must never be auto —
    it is the difference between typing a note and running a command."""
    _type_into(monkeypatch, title)
    info = jinput.classify_type({"text": "rm -rf /", "window": title})
    assert info["tier"] == "confirm", f"{title} must confirm, got {info}"


def test_type_confirm_carries_the_verbatim_payload(monkeypatch):
    """Slice 38's whole point: the modal must show WHAT is typed, not just
    WHERE. A confirm naming only the window is approved blind."""
    _type_into(monkeypatch, "Windows PowerShell")
    info = jinput.classify_type({"text": "Remove-Item -Recurse C:\\data",
                                 "window": "PowerShell"})
    assert "Remove-Item" in (info.get("command") or ""), info


def test_type_with_no_window_still_classifies(monkeypatch):
    """Hostile/degenerate input must not raise — the gate has to decide."""
    monkeypatch.setattr(jinput, "_target_window", lambda w: (None, None))
    for args in ({}, {"text": None}, {"text": "", "window": None}):
        assert jinput.classify_type(args)["tier"] in VALID_TIERS


# ============================ classify_web_key ============================

def _web_mode(monkeypatch, *, blocked=False, user_accounts=False):
    monkeypatch.setattr(web, "_actions_blocked", lambda: blocked)
    monkeypatch.setattr(web, "_on_user_accounts", lambda: user_accounts)
    monkeypatch.setattr(web, "_submit_payload", lambda: "the field contents")
    monkeypatch.setattr(web, "_site_host", lambda: " on example.com")


def test_web_key_is_blocked_when_actions_are_blocked(monkeypatch):
    _web_mode(monkeypatch, blocked=True)
    assert web.classify_web_key({"key": "Enter"})["tier"] == "blocked"


@pytest.mark.parametrize("key", ["Tab", "Escape", "ArrowDown", "ArrowUp"])
def test_navigation_keys_are_auto_even_on_the_users_accounts(monkeypatch, key):
    """Prompt fatigue is its own safety problem — non-committal keys must not
    ask, or users learn to click through the ones that matter."""
    _web_mode(monkeypatch, user_accounts=True)
    assert web.classify_web_key({"key": key})["tier"] == "auto"


def test_enter_on_the_users_accounts_confirms_and_shows_what_it_submits(monkeypatch):
    """THE SLICE-38 HOLE: browse_fill + browse_key('Enter') used to post a form
    on the user's real account with no gate, while clicking 'Submit' on the same
    form WAS gated."""
    _web_mode(monkeypatch, user_accounts=True)
    info = web.classify_web_key({"key": "Enter"})
    assert info["tier"] == "confirm", info
    assert info.get("command"), "the confirm must show WHAT is being submitted"


def test_enter_in_the_isolated_browser_stays_auto(monkeypatch):
    """Deliberate owner decision, pinned so it reads as a choice not an
    oversight: the isolated browser starts logged out, so a stray submit commits
    nothing of the user's."""
    _web_mode(monkeypatch, user_accounts=False)
    assert web.classify_web_key({"key": "Enter"})["tier"] == "auto"


@pytest.mark.parametrize("key", ["ENTER", "enter", " Enter "])
def test_enter_is_gated_regardless_of_casing_or_padding(monkeypatch, key):
    """Case/whitespace must not be a bypass — the model's exact spelling varies."""
    _web_mode(monkeypatch, user_accounts=True)
    assert web.classify_web_key({"key": key})["tier"] == "confirm", key


# ======================== classify_create_shortcut ========================

def test_create_shortcut_into_a_protected_dir_is_blocked():
    info = fsaccess.classify_create_shortcut(
        {"target": r"C:\Users\me\thing.txt", "location": r"C:\Windows\System32"})
    assert info["tier"] == "blocked", info


def test_create_shortcut_normally_confirms_and_names_both_ends():
    info = fsaccess.classify_create_shortcut(
        {"target": r"C:\Users\me\report.pdf", "location": "desktop"})
    assert info["tier"] == "confirm", info
    assert "report.pdf" in (info.get("command") or "") + info["description"]


def test_create_shortcut_never_returns_auto():
    """Writing a file into a user directory is never a silent action."""
    for args in ({}, {"target": ""}, {"target": "x", "location": "downloads"}):
        assert fsaccess.classify_create_shortcut(args)["tier"] in ("confirm", "blocked")


def test_create_shortcut_fails_closed_when_classification_raises(monkeypatch):
    """Doctrine: unknown -> CONFIRM. An exception must never become auto."""
    def boom(_p):
        raise RuntimeError("resolver exploded")
    monkeypatch.setattr(fsaccess, "resolve_user_path", boom)
    assert fsaccess.classify_create_shortcut(
        {"target": "x", "location": "desktop"})["tier"] == "confirm"


# ========================= classify_rename_path =========================

def test_rename_a_protected_path_is_blocked():
    info = fsaccess.classify_rename_path(
        {"path": r"C:\Windows\System32", "new_name": "junk"})
    assert info["tier"] == "blocked", info


def test_rename_confirms_and_shows_both_names(tmp_path):
    victim = tmp_path / "notes.txt"
    victim.write_text("x", encoding="utf-8")
    info = fsaccess.classify_rename_path(
        {"path": str(victim), "new_name": "renamed.txt"})
    assert info["tier"] == "confirm", info
    assert "renamed.txt" in (info.get("command") or "") + info["description"]


def test_rename_traversal_reaching_a_protected_tree_is_blocked():
    """HOSTILE INPUT, layer 1. `dest = path.parent / new_name`, so a new_name
    carrying ../.. escapes the source's directory. classify_path_risk resolves
    before judging, so the destination is caught on its REAL location.

    The depth here is deliberate: C:\\Users\\malek + ..\\.. lands exactly on
    C:\\, so the traversal genuinely reaches C:\\Windows\\System32. An earlier
    draft of this test used tmp_path and four ..'s, which resolved to
    AppData\\Local\\Windows\\System32 — not protected, so 'confirm' was the
    CORRECT answer and the test was wrong, not the code."""
    info = fsaccess.classify_rename_path(
        {"path": r"C:\Users\malek\notes.txt",
         "new_name": r"..\..\Windows\System32\evil.dll"})
    assert info["tier"] == "blocked", \
        f"a traversal reaching a protected dir must block: {info}"


def test_rename_execution_refuses_a_path_like_new_name():
    """HOSTILE INPUT, layer 2 — defence in depth. Independently of the tier,
    rename_path() refuses any new_name containing a separator or '..', so the
    traversal cannot execute even if a classifier were ever weakened."""
    for bad in (r"..\..\Windows\System32\evil.dll", "sub/dir/x.txt",
                r"sub\x.txt", ".."):
        r = fsaccess.rename_path(r"C:\Users\malek\notes.txt", bad)
        assert r["ok"] is False, f"{bad!r} must be refused: {r}"
        assert "just a new name" in r["message"], r


def test_rename_never_returns_auto():
    for args in ({}, {"path": ""}, {"path": "x", "new_name": ""},
                 {"path": None, "new_name": None}):
        assert fsaccess.classify_rename_path(args)["tier"] in ("confirm", "blocked")


def test_rename_fails_closed_when_classification_raises(monkeypatch):
    def boom(_p):
        raise RuntimeError("resolver exploded")
    monkeypatch.setattr(fsaccess, "resolve_user_path", boom)
    assert fsaccess.classify_rename_path(
        {"path": "x", "new_name": "y"})["tier"] == "confirm"


# ===================== cross-cutting: the shared contract =====================

def test_all_four_classifiers_return_a_valid_tier_on_garbage(monkeypatch):
    """A malformed tier string does not fail closed by accident — primitives.
    execute() only treats the exact literals as meaningful, so an unknown value
    is the dangerous case. Every classifier must return one of the three."""
    monkeypatch.setattr(jinput, "_target_window", lambda w: (None, "x"))
    _web_mode(monkeypatch)
    garbage = [{}, {"key": None}, {"text": 123}, {"path": 0, "new_name": []},
               {"target": None, "location": None}]
    for fn in (jinput.classify_type, web.classify_web_key,
               fsaccess.classify_create_shortcut, fsaccess.classify_rename_path):
        for args in garbage:
            info = fn(args)
            assert isinstance(info, dict), (fn.__name__, args, info)
            assert info.get("tier") in VALID_TIERS, (fn.__name__, args, info)
