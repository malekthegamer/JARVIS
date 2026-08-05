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
    """Pin every source. The Start Menu is emptied by pointing the REAL walk at
    nothing rather than stubbing start_menu_apps() out — otherwise the tests
    that exist to exercise that walk would silently test the stub instead."""
    from jarvis.primitives import apps

    monkeypatch.setattr(D, "desktop_shortcuts",
                        lambda: [e for e in INVENTORY if e["source"] == "desktop"])
    monkeypatch.setattr(D, "steam_games",
                        lambda: [e for e in INVENTORY if e["source"] == "steam"])
    monkeypatch.setattr(D, "epic_games", lambda: [])
    monkeypatch.setattr(apps, "_START_MENU_DIRS", [])


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


# ------------------------------------------------------ the Start Menu source

def test_start_menu_apps_are_discoverable(monkeypatch, tmp_path):
    """apps.py walks the Start Menu, but only for an EXACT '<name>.lnk', so
    find()/suggest() could never fuzzy-match one. Measured on the owner's
    machine: 145 apps resolve by their exact name and not one of them could be
    SUGGESTED after a near-miss — the slice-60 dead end, still open for them.
    """
    from jarvis.primitives import apps

    real = tmp_path / "AMD Software.exe"
    real.write_text("x", encoding="utf-8")
    lnk = tmp_path / "AMD Software\ua789 Adrenalin Edition.lnk"
    lnk.write_text("", encoding="utf-8")

    monkeypatch.setattr(apps, "_START_MENU_DIRS", [str(tmp_path)])
    monkeypatch.setattr(apps, "_lnk_target", lambda p: str(real))
    entries = D.start_menu_apps()
    assert entries, "Start Menu produced nothing"
    assert any("Adrenalin" in e["name"] for e in entries), entries
    assert all(e["source"] == "start_menu" for e in entries), entries


def test_a_stale_start_menu_shortcut_is_ignored(monkeypatch, tmp_path):
    """A .lnk pointing at an uninstalled app must not become a launch target.
    This is the exact mistake the AppCompatFlags graveyard just taught me —
    a leftover record is not an inventory."""
    from jarvis.primitives import apps

    (tmp_path / "Ghost App.lnk").write_text("", encoding="utf-8")
    monkeypatch.setattr(apps, "_START_MENU_DIRS", [str(tmp_path)])
    monkeypatch.setattr(apps, "_lnk_target",
                        lambda p: str(tmp_path / "does-not-exist.exe"))

    assert D.start_menu_apps() == []


def test_suggest_offers_start_menu_apps(monkeypatch, tmp_path):
    """The point of the whole stage: a near-miss must offer a real name back."""
    from jarvis.primitives import apps

    real = tmp_path / "Blender.exe"
    real.write_text("x", encoding="utf-8")
    (tmp_path / "Blender 4.5.lnk").write_text("", encoding="utf-8")
    monkeypatch.setattr(apps, "_START_MENU_DIRS", [str(tmp_path)])
    monkeypatch.setattr(apps, "_lnk_target", lambda p: str(real))

    assert any("Blender" in s for s in D.suggest("blendr")), D.suggest("blendr")


def test_the_start_menu_scan_is_cached(monkeypatch, tmp_path):
    """Resolving a .lnk is a COM round trip EACH, ~160 of them — measured 280ms,
    and find() plus suggest() both call it, so an uncached miss paid it twice.

    This test exists because the first cache I wrote did nothing: the loop
    rebound `key` (the cache key) to each shortcut's path, so entries were
    stored under the last .lnk instead of the directory tuple and never hit.
    """
    from jarvis.primitives import apps

    real = tmp_path / "Thing.exe"
    real.write_text("x", encoding="utf-8")
    (tmp_path / "Thing.lnk").write_text("", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(apps, "_START_MENU_DIRS", [str(tmp_path)])
    monkeypatch.setattr(apps, "_lnk_target",
                        lambda p: calls.append(p) or str(real))
    monkeypatch.setattr(D, "_SM_CACHE", {})

    first = D.start_menu_apps()
    n_after_first = len(calls)
    second = D.start_menu_apps()

    assert first == second and first, first
    assert len(calls) == n_after_first, (
        f"scan re-ran: {len(calls)} .lnk resolutions instead of {n_after_first}")


def test_the_cache_does_not_leak_between_different_start_menus(monkeypatch, tmp_path):
    """Keyed on the directories, so a test (or a settings change) that repoints
    the scan can never read another location's result."""
    from jarvis.primitives import apps

    a, b = tmp_path / "a", tmp_path / "b"
    for d, exe in ((a, "Alpha"), (b, "Beta")):
        d.mkdir()
        (d / f"{exe}.exe").write_text("x", encoding="utf-8")
        (d / f"{exe}.lnk").write_text("", encoding="utf-8")
    monkeypatch.setattr(D, "_SM_CACHE", {})
    monkeypatch.setattr(apps, "_lnk_target",
                        lambda p: p[:-4] + ".exe")

    monkeypatch.setattr(apps, "_START_MENU_DIRS", [str(a)])
    assert [e["name"] for e in D.start_menu_apps()] == ["Alpha"]
    monkeypatch.setattr(apps, "_START_MENU_DIRS", [str(b)])
    assert [e["name"] for e in D.start_menu_apps()] == ["Beta"]


# ------------------------------------------------- stage 3: honest ambiguity

def test_resolve_app_passes_candidates_through():
    """resolve_app DISCARDED find()'s candidates and returned a bare None, so
    everything downstream was told 'nothing matched' when three things had."""
    from jarvis.primitives import apps

    target, matched, candidates = apps.resolve_app_detail("resident evil")
    assert target is None, target
    assert len(candidates) == 3, candidates
    assert all("Resident Evil" in c for c in candidates), candidates


def test_resolve_app_keeps_its_two_value_shape():
    """Existing callers must not break: resolve_app stays (target, matched)."""
    from jarvis.primitives import apps

    target, matched = apps.resolve_app("resident evil 4")
    assert target and "re4" in target.lower(), (target, matched)


def test_the_ambiguous_message_does_not_claim_nothing_was_found(monkeypatch):
    """The lie, same class as slice 63's 'doesn't appear to be installed': it
    found three Resident Evils and reported that nothing was named that."""
    from jarvis.primitives import apps

    r = apps.launch_app("resident evil")
    assert r["ok"] is False
    msg = r["message"].lower()
    assert "no application named" not in msg, r["message"]
    assert "resident evil 2" in msg and "resident evil 4" in msg, r["message"]
    assert "which" in msg or "several" in msg, r["message"]


def test_a_genuine_miss_still_says_it_found_nothing(monkeypatch):
    """The honest 'not found' must survive — only the FALSE one goes."""
    from jarvis.primitives import apps

    r = apps.launch_app("no such application anywhere")
    assert r["ok"] is False
    assert "no application named" in r["message"].lower(), r["message"]


def test_the_spoken_choice_does_not_read_out_source_tags(monkeypatch):
    """find() tags candidates '(desktop)'/'(steam)' — useful in a log, noise
    when JARVIS SAYS it out loud."""
    from jarvis.primitives import apps

    msg = apps.launch_app("resident evil")["message"]
    assert "(desktop)" not in msg and "(steam)" not in msg, msg
    assert "Resident Evil 2" in msg, msg
