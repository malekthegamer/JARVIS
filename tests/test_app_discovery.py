"""Slice 22 Part A — app discovery (desktop shortcuts + Steam + Epic).

launch_app couldn't find Rocket League (Epic, manifest AppName "Sugar") or
anything living only as a desktop shortcut — no App Paths key, no PATH
entry, no exact-name Start Menu .lnk (probe-confirmed root cause). These
tests pin the discovery fallback: deterministic fixture trees only, no real
Steam/Epic/desktop touched, and the launch-safety doctrine — a name that
matches MORE than one thing returns candidates and launches NOTHING.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.primitives import app_discovery as disco
from jarvis.primitives import apps


# ------------------------------------------------------------- fixtures

@pytest.fixture()
def steam_root(tmp_path, monkeypatch):
    """A fake Steam tree: root library + one extra library (listed twice in
    the vdf with different casing — the real machine's dupe, must dedupe)."""
    root = tmp_path / "steam"
    lib2 = tmp_path / "SteamLibrary"
    for lib, apps_ in [(root, [("291550", "Brawlhalla")]),
                       (lib2, [("252950", "Rocket League"),
                               ("228980", "Steamworks Common Redistributables")])]:
        sa = lib / "steamapps"
        sa.mkdir(parents=True)
        for appid, name in apps_:
            (sa / f"appmanifest_{appid}.acf").write_text(
                f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n'
                f'\t"name"\t\t"{name}"\n}}\n', encoding="utf-8")
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n'
        f'\t"0"\n\t{{\n\t\t"path"\t\t"{str(root).replace(chr(92), chr(92)*2)}"\n\t}}\n'
        f'\t"1"\n\t{{\n\t\t"path"\t\t"{str(lib2).replace(chr(92), chr(92)*2)}"\n\t}}\n'
        f'\t"2"\n\t{{\n\t\t"path"\t\t"{str(lib2).upper().replace(chr(92), chr(92)*2)}"\n\t}}\n'
        "}\n", encoding="utf-8")
    monkeypatch.setattr(disco, "_steam_root", lambda: str(root))
    return root


@pytest.fixture()
def epic_manifests(tmp_path, monkeypatch):
    mdir = tmp_path / "EpicManifests"
    mdir.mkdir()
    (mdir / "sugar.item").write_text(json.dumps({
        "DisplayName": "Rocket League®",     # the real ® from the probe
        "AppName": "Sugar",
        "InstallLocation": r"E:\Epic\rocketleague",
        "LaunchExecutable": "Binaries/Win64/Launcher.exe",
    }), encoding="utf-8")
    monkeypatch.setattr(disco, "EPIC_MANIFEST_DIR", str(mdir))
    return mdir


@pytest.fixture()
def desktop(tmp_path, monkeypatch):
    d = tmp_path / "Desktop"
    d.mkdir()
    (d / "Apex Legends.url").write_text(
        "[InternetShortcut]\nIDList=\nURL=steam://rungameid/1172470\n"
        "IconIndex=0\n", encoding="utf-8")
    (d / "MyTool.lnk").write_bytes(b"not a real lnk")
    (d / "Broken.lnk").write_bytes(b"also not real")
    real_target = tmp_path / "mytool.exe"
    real_target.write_bytes(b"MZ")
    monkeypatch.setattr(disco, "DESKTOP_DIRS", [str(d)])
    monkeypatch.setattr(
        apps, "_lnk_target",
        lambda p: str(real_target) if "MyTool" in p else None)
    return d


@pytest.fixture()
def nothing(monkeypatch, tmp_path):
    """All sources absent/empty — discovery must degrade to nothing found."""
    monkeypatch.setattr(disco, "_steam_root", lambda: None)
    monkeypatch.setattr(disco, "EPIC_MANIFEST_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(disco, "DESKTOP_DIRS", [str(tmp_path / "nodesk")])


# ------------------------------------------------------------- parsing

def test_steam_games_parsed_and_deduped(steam_root, epic_manifests, desktop):
    games = disco.steam_games()
    by_name = {g["name"]: g for g in games}
    assert by_name["Brawlhalla"]["launch"] == "steam://rungameid/291550"
    assert by_name["Rocket League"]["launch"] == "steam://rungameid/252950"
    # the twice-listed library must not double the entries
    assert len([g for g in games if g["name"] == "Rocket League"]) == 1
    # redistributables are plumbing, not launchable apps
    assert "Steamworks Common Redistributables" not in by_name


def test_epic_games_parsed_with_trademark_name(epic_manifests):
    (game,) = disco.epic_games()
    assert game["launch"] == ("com.epicgames.launcher://apps/Sugar"
                              "?action=launch&silent=true")
    assert "Rocket League" in game["name"]


def test_desktop_url_shortcut_gives_steam_uri(desktop):
    entries = disco.desktop_shortcuts()
    apex = next(e for e in entries if "apex" in e["name"].lower())
    assert apex["launch"] == "steam://rungameid/1172470"


def test_desktop_lnk_resolved_and_broken_skipped(desktop):
    entries = disco.desktop_shortcuts()
    names = [e["name"].lower() for e in entries]
    assert "mytool" in names          # resolvable .lnk kept (real target)
    assert "broken" not in names      # unresolvable .lnk skipped


# ------------------------------------------------------------- matching

def test_find_matches_despite_trademark_symbol(steam_root, epic_manifests, desktop, monkeypatch):
    # steam fixture also has "Rocket League" -> ambiguity with Epic's ®-name
    # would be real; drop steam here to isolate the normalization claim.
    monkeypatch.setattr(disco, "_steam_root", lambda: None)
    hit = disco.find("rocket league")
    assert hit and hit.get("launch", "").startswith("com.epicgames.launcher://apps/Sugar")


def test_find_exact_beats_substring(steam_root, epic_manifests, desktop, monkeypatch):
    monkeypatch.setattr(disco, "EPIC_MANIFEST_DIR", "missing")
    hit = disco.find("brawlhalla")
    assert hit and hit["launch"] == "steam://rungameid/291550"


def test_find_same_app_on_two_sources_resolves(steam_root, epic_manifests, desktop):
    """'Rocket League' exists on steam AND epic (same normalized name = the
    same app the user means) — that is NOT ambiguity; it must resolve, by
    source priority steam > epic > desktop."""
    hit = disco.find("rocket league")
    assert hit and hit.get("launch") == "steam://rungameid/252950", hit


def test_find_ambiguous_different_apps_returns_candidates(tmp_path, monkeypatch, epic_manifests):
    """GENUINELY different apps sharing a needle must NOT guess-launch."""
    monkeypatch.setattr(disco, "_steam_root", lambda: None)
    d = tmp_path / "desk2"
    d.mkdir()
    (d / "Call of Duty Black Ops II.url").write_text(
        "[InternetShortcut]\nURL=steam://rungameid/202990\n", encoding="utf-8")
    (d / "Call of Duty Black Ops III.url").write_text(
        "[InternetShortcut]\nURL=steam://rungameid/311210\n", encoding="utf-8")
    monkeypatch.setattr(disco, "DESKTOP_DIRS", [str(d)])
    hit = disco.find("call of duty")
    assert hit is not None
    assert "launch" not in hit, f"different apps must not guess-launch: {hit}"
    assert len(hit["candidates"]) == 2


def test_find_unknown_returns_none(steam_root, epic_manifests, desktop):
    assert disco.find("xyzzy-no-such-app") is None


def test_all_sources_missing_graceful(nothing):
    assert disco.steam_games() == []
    assert disco.epic_games() == []
    assert disco.desktop_shortcuts() == []
    assert disco.find("anything") is None


# ------------------------------------------------------- resolve_app rung

def test_resolve_app_falls_back_to_discovery(monkeypatch):
    monkeypatch.setattr(disco, "find",
                        lambda name: {"launch": "steam://rungameid/252950",
                                      "name": "Rocket League", "source": "steam"})
    target, _ = apps.resolve_app("rocket league")
    assert target == "steam://rungameid/252950"


def test_resolve_app_unknown_still_fails_closed(monkeypatch, nothing):
    monkeypatch.setattr(disco, "find", lambda name: None)
    target, _ = apps.resolve_app("xyzzy-no-such-app")
    assert target is None


# ------------------------------------------------- A2: executor wire-in

from jarvis import primitives
from jarvis.core.settings_store import settings as _settings


@pytest.fixture(autouse=True)
def _broadcaster_back_to_idle():
    """Leak guard (test_audit/test_shell pattern): execute() outside think()
    parks the broadcaster at THINKING; reset so file order never matters."""
    yield
    from jarvis.state import AgentState, broadcaster
    broadcaster.set(AgentState.IDLE)


def _fake_launch(monkeypatch, resolved, matched):
    monkeypatch.setattr(apps, "launch_app", lambda name: {
        "ok": True, "pid": None, "resolved": resolved, "matched": matched,
        "message": f"Opened {resolved}."})
    import numpy as np
    frame = np.zeros((4, 4, 3), dtype="uint8")
    monkeypatch.setattr(primitives.screen, "capture_screen", lambda *a, **k: frame)
    monkeypatch.setattr(primitives.screen, "screenshot_diff", lambda a, b: 0.0)


def test_game_uri_launch_verified_by_window(monkeypatch):
    _fake_launch(monkeypatch, "steam://rungameid/999", "Rocket League®")
    seen = {}
    monkeypatch.setattr(primitives.ui_tree, "window_present",
                        lambda needle: seen.setdefault("needle", needle) or True)
    out = primitives.execute("launch_app", {"name": "rocket league"})
    assert "VERIFIED" in out, out
    assert seen["needle"] == "rocket league"   # normalized name, ® stripped


def test_game_uri_launch_honest_when_window_never_appears(monkeypatch):
    _fake_launch(monkeypatch, "com.epicgames.launcher://apps/Sugar?action=launch",
                 "Rocket League®")
    monkeypatch.setattr(primitives.ui_tree, "window_present", lambda n: False)
    monkeypatch.setattr(primitives.ui_tree, "window_present_for_process",
                        lambda e: False)
    _settings.set("apps.game_window_wait_s", 0.5, persist=False)
    try:
        out = primitives.execute("launch_app", {"name": "rocket league"})
    finally:
        _settings.set("apps.game_window_wait_s", 20, persist=False)
    assert "hasn't appeared yet" in out, out          # honest dispatch, not silence
    assert "VERIFIED" not in out.split("VERIFY")[0]   # never a false OK
    assert "NOT CONFIRMED" in out or "DISPATCHED" in out


def test_plain_uri_launch_keeps_old_behavior(monkeypatch):
    _fake_launch(monkeypatch, "ms-settings:", "settings")
    called = {"n": 0}
    monkeypatch.setattr(primitives.ui_tree, "window_present",
                        lambda n: called.__setitem__("n", called["n"] + 1) or False)
    out = primitives.execute("launch_app", {"name": "settings"})
    assert called["n"] == 0, "non-game URIs must not gain a window poll"
    assert out.startswith("Opened ms-settings:")


# ------------------------------------------- folder shortcuts (A3 finding)

def test_desktop_lnk_to_folder_kept(tmp_path, monkeypatch):
    """The REAL desktop had ArtTuneDB.lnk -> a FOLDER; 'open X' on a folder
    shortcut must open it in Explorer, not vanish from discovery."""
    d = tmp_path / "desk3"
    d.mkdir()
    (d / "MyDocs.lnk").write_bytes(b"fake")
    target_dir = tmp_path / "real_folder"
    target_dir.mkdir()
    monkeypatch.setattr(disco, "DESKTOP_DIRS", [str(d)])
    monkeypatch.setattr(disco, "_steam_root", lambda: None)
    monkeypatch.setattr(disco, "EPIC_MANIFEST_DIR", "missing")
    monkeypatch.setattr(apps, "_lnk_target", lambda p: str(target_dir))
    hit = disco.find("mydocs")
    assert hit and hit["launch"] == str(target_dir), hit


def test_launch_app_opens_folder_via_startfile(tmp_path, monkeypatch):
    folder = tmp_path / "somefolder"
    folder.mkdir()
    monkeypatch.setattr(apps, "resolve_app",
                        lambda name: (str(folder), "somefolder"))
    opened = []
    monkeypatch.setattr(apps.os, "startfile", lambda t: opened.append(t),
                        raising=False)
    r = apps.launch_app("somefolder")
    assert r["ok"] and opened == [str(folder)], r
    assert r["pid"] is None           # Explorer owns it; no pid to claim
