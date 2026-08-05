"""Slice 64 — the app-name matching matrix, on a FAKE inventory.

tests/harness_app_resolve_eval.py scores the real machine; this pins the
patterns. Deterministic on purpose: a test that encodes one person's Steam
library is a test that fails for everybody else.

Every case here came from a real miss measured on the owner's machine:

    prismlauncher   a missing space
    spider-man 2    'Spider-Man2' — a digit welded to the word
    black ops 2     '2' where the shortcut says 'II'
    fifa            lost to 'FIFA 22 Settings'
    resident evil   three real games — asking is the RIGHT answer, not a bug
"""
from __future__ import annotations

import pytest

from jarvis.primitives import app_discovery as D

INVENTORY = [
    {"name": "Call of Duty Black Ops II", "source": "desktop", "launch": r"D:\g\t6sp.exe"},
    {"name": "Call of Duty Black Ops II Multiplayer", "source": "desktop", "launch": r"D:\g\t6mp.exe"},
    {"name": "Spider-Man2.exe - Shortcut", "source": "desktop", "launch": r"E:\g\Spider-Man2.exe"},
    {"name": "Prism Launcher (Cracked)", "source": "desktop", "launch": r"C:\p\prismlauncher.exe"},
    {"name": "FIFA 22", "source": "desktop", "launch": r"G:\FIFA 22\FIFA22.exe"},
    {"name": "FIFA 22 Settings", "source": "desktop", "launch": r"G:\FIFA 22\fifaconfig.exe"},
    {"name": "Resident Evil 2", "source": "desktop", "launch": r"E:\g\re2.exe"},
    {"name": "Resident Evil 3", "source": "desktop", "launch": r"E:\g\re3.exe"},
    {"name": "Resident Evil 4", "source": "desktop", "launch": r"E:\g\re4.exe"},
    {"name": "Civilization VI", "source": "steam", "launch": "steam://rungameid/289070"},
    {"name": "X", "source": "desktop", "launch": r"C:\p\x.exe"},
    {"name": "Vim", "source": "desktop", "launch": r"C:\p\vim.exe"},
]


@pytest.fixture(autouse=True)
def fake_inventory(monkeypatch):
    monkeypatch.setattr(D, "desktop_shortcuts",
                        lambda: [e for e in INVENTORY if e["source"] == "desktop"])
    monkeypatch.setattr(D, "steam_games",
                        lambda: [e for e in INVENTORY if e["source"] == "steam"])
    monkeypatch.setattr(D, "epic_games", lambda: [])
    if hasattr(D, "start_menu_apps"):
        monkeypatch.setattr(D, "start_menu_apps", lambda: [])


def _names(hit) -> list[str]:
    """A find() result as a flat list of names, whatever shape it came in."""
    if not hit:
        return []
    if hit.get("candidates"):
        return [c.split(" (")[0] for c in hit["candidates"]]
    return [hit["name"]]


# ------------------------------------------------------------ normalization

def test_roman_numerals_match_arabic():
    """'black ops 2' must reach 'Black Ops II'. Both Black Ops entries match,
    so the right outcome is a CHOICE — but it must no longer be a dead end."""
    got = _names(D.find("black ops 2"))
    assert got, "'black ops 2' found nothing — the II/2 gap"
    assert all("Black Ops II" in n for n in got), got


def test_a_digit_stuck_to_a_word_is_split():
    """'Spider-Man2.exe - Shortcut' — the digit is welded to the word, so a
    spoken 'spider-man 2' never matched."""
    assert _names(D.find("spider-man 2")) == ["Spider-Man2.exe - Shortcut"]
    assert _names(D.find("spiderman 2")) == ["Spider-Man2.exe - Shortcut"]


def test_spaces_are_optional():
    """What the owner literally typed this session: 'open prism launcher' works,
    'prismlauncher' did not."""
    assert _names(D.find("prismlauncher")) == ["Prism Launcher (Cracked)"]


def test_bare_i_v_x_are_not_treated_as_roman_numerals():
    """Deliberate limit. 'X' and 'Vim' are real names; turning a lone 'v' into
    '5' would corrupt more than it fixes. Cost: 'gta v' stays unnormalized."""
    assert _names(D.find("x")) == ["X"]
    assert _names(D.find("vim")) == ["Vim"]
    assert D._norm("Civilization VI") == "civilization 6"
    assert D._norm("X") == "x"
    assert D._norm("Vim") == "vim"


def test_normalization_does_not_merge_unrelated_apps():
    """Guard against a loosening that makes everything match everything."""
    assert _names(D.find("civilization 6")) == ["Civilization VI"]
    assert not _names(D.find("totally absent app"))


# ------------------------------------------------------- auxiliary entries

def test_an_auxiliary_entry_never_wins_alone():
    """'fifa' is one game plus its config tool, not two games."""
    assert _names(D.find("fifa")) == ["FIFA 22"]


def test_asking_for_the_auxiliary_still_finds_it():
    """Suppression must not HIDE it — only stop it creating false ambiguity."""
    assert _names(D.find("fifa settings")) == ["FIFA 22 Settings"]


# -------------------------------------------------------- genuine ambiguity

def test_genuine_ambiguity_still_refuses_to_guess():
    """The documented doctrine stands: a wrong game fullscreening the machine is
    worse than a clean question. This slice does NOT loosen it."""
    hit = D.find("resident evil")
    assert hit and hit.get("candidates"), hit
    assert len(hit["candidates"]) == 3, hit
    assert "launch" not in hit, "ambiguity must never carry a launch target"
