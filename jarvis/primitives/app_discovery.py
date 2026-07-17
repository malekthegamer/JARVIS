"""App discovery (slice 22) — the fallback rung after apps.py's fast ladder.

Finds launchable things that never register anywhere the fast ladder looks
(probe-confirmed root cause for "can't open Rocket League"): desktop
shortcuts (.lnk resolved to their real target, .url parsed for their URI),
installed Steam games (registry root -> libraryfolders.vdf -> appmanifest
files), and installed Epic games (ProgramData manifests).

API-first (CLAUDE.md §1): the documented launch interface for Steam/Epic
titles is their URI protocol — `steam://rungameid/<id>` and
`com.epicgames.launcher://apps/<AppName>?action=launch&silent=true` — not
raw .exe paths (Steam's own desktop .url shortcuts use the URI; Epic
binaries routinely fail DRM without the launcher). Entries carry those URIs
and flow through apps.py's existing _is_uri/os.startfile branch unchanged.

Safety doctrine (resolve_target/forget precedent): find() launches ONLY on
a unique match. Ambiguity returns candidates and launches nothing — a wrong
game fullscreening the machine is worse than a clean question. Every scan
degrades to [] on failure; this module never raises.
"""
from __future__ import annotations

import glob
import json
import os
import re

# Module-level so tests (and odd setups) can repoint them.
EPIC_MANIFEST_DIR = r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests"
DESKTOP_DIRS = [
    os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
    r"C:\Users\Public\Desktop",
]

_STEAM_SKIP = {"steamworks common redistributables"}


def _steam_root() -> str | None:
    """Steam's install root from the registry — NOT the default path (this
    machine: e:/steam; probe-confirmed the default guess finds nothing)."""
    try:
        import winreg
        for root, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(root, key) as k:
                    for value in ("SteamPath", "InstallPath"):
                        try:
                            path = winreg.QueryValueEx(k, value)[0]
                            if path and os.path.isdir(path):
                                return path
                        except OSError:
                            continue
            except OSError:
                continue
    except Exception:
        pass
    return None


def steam_games() -> list[dict]:
    """Installed Steam games across every library folder in
    libraryfolders.vdf (deduped case-insensitively — real machines list the
    same library twice with different casing)."""
    root = _steam_root()
    if not root:
        return []
    out: list[dict] = []
    try:
        libs = [os.path.join(root, "steamapps")]
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        if os.path.isfile(vdf):
            text = open(vdf, encoding="utf-8", errors="replace").read()
            for p in re.findall(r'"path"\s+"([^"]+)"', text):
                libs.append(os.path.join(p.replace("\\\\", "\\"), "steamapps"))
        seen_libs, seen_ids = set(), set()
        for lib in libs:
            key = os.path.normcase(os.path.normpath(lib))
            if key in seen_libs:
                continue
            seen_libs.add(key)
            for mf in glob.glob(os.path.join(lib, "appmanifest_*.acf")):
                try:
                    t = open(mf, encoding="utf-8", errors="replace").read()
                    appid = re.search(r'"appid"\s+"(\d+)"', t)
                    name = re.search(r'"name"\s+"([^"]+)"', t)
                except OSError:
                    continue
                if not (appid and name) or appid.group(1) in seen_ids:
                    continue
                if name.group(1).casefold() in _STEAM_SKIP:
                    continue
                seen_ids.add(appid.group(1))
                out.append({"name": name.group(1), "source": "steam",
                            "launch": f"steam://rungameid/{appid.group(1)}"})
    except Exception:
        return []
    return out


def epic_games() -> list[dict]:
    """Installed Epic games from the launcher's manifest files."""
    out: list[dict] = []
    try:
        if not os.path.isdir(EPIC_MANIFEST_DIR):
            return []
        for f in glob.glob(os.path.join(EPIC_MANIFEST_DIR, "*.item")):
            try:
                d = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            name, appname = d.get("DisplayName"), d.get("AppName")
            if name and appname:
                out.append({"name": name, "source": "epic",
                            "launch": (f"com.epicgames.launcher://apps/{appname}"
                                       f"?action=launch&silent=true")})
    except Exception:
        return []
    return out


def desktop_shortcuts() -> list[dict]:
    """User + public desktop: .url parsed for its URL= line (Steam's own
    shortcuts carry steam:// URIs), .lnk resolved to a real existing target
    via apps._lnk_target (module attr — reused, and test-swappable)."""
    from jarvis.primitives import apps  # late: avoid import cycle
    out: list[dict] = []
    for d in DESKTOP_DIRS:
        try:
            if not os.path.isdir(d):
                continue
            for fn in os.listdir(d):
                stem, ext = os.path.splitext(fn)
                ext = ext.lower()
                full = os.path.join(d, fn)
                if ext == ".url":
                    try:
                        text = open(full, encoding="utf-8",
                                    errors="replace").read()
                    except OSError:
                        continue
                    m = re.search(r"^URL=(.+)$", text, re.MULTILINE)
                    if m:
                        out.append({"name": stem, "source": "desktop",
                                    "launch": m.group(1).strip()})
                elif ext == ".lnk":
                    target = apps._lnk_target(full)
                    # Files AND folders: real desktops shortcut both (the
                    # A3 acceptance found ArtTuneDB.lnk -> a config FOLDER);
                    # os.startfile opens a folder in Explorer.
                    if target and (os.path.isfile(target)
                                   or os.path.isdir(target)):
                        out.append({"name": stem, "source": "desktop",
                                    "launch": target})
        except Exception:
            continue
    return out


def _norm(text: str) -> str:
    """Casefold; every non-alphanumeric (®, ™, dashes, dots) becomes a space
    — probe-driven: Epic's DisplayName is literally 'Rocket League®'."""
    return " ".join("".join(c if c.isalnum() else " "
                            for c in str(text or "").casefold()).split())


def find(name: str) -> dict | None:
    """Unique match across all sources, or candidates, or None.
    Precedence: exact normalized name > unique prefix > unique substring.
    >1 match at the winning tier => {"candidates": [...]} and NO launch."""
    needle = _norm(name)
    if not needle:
        return None
    entries = desktop_shortcuts() + steam_games() + epic_games()
    # Same normalized name from multiple sources = the same thing the user
    # means; prefer desktop < steam < epic order stability but dedupe only
    # EXACT-equal launch targets (a steam game and its own .url shortcut).
    uniq: list[dict] = []
    seen_launch = set()
    for e in entries:
        key = (e["launch"] or "").casefold()
        if key in seen_launch:
            continue
        seen_launch.add(key)
        uniq.append(e)

    _PRIORITY = {"steam": 0, "epic": 1, "desktop": 2}
    for tier in ("exact", "prefix", "substring"):
        hits = []
        for e in uniq:
            n = _norm(e["name"])
            if ((tier == "exact" and n == needle)
                    or (tier == "prefix" and n.startswith(needle))
                    or (tier == "substring" and needle in n)):
                hits.append(e)
        if not hits:
            continue
        # Hits sharing ONE normalized name are the SAME app discovered via
        # several sources (RL on steam+epic, a game plus its own desktop
        # shortcut) — not ambiguity. Resolve by source priority (URIs are
        # the launchers' documented interface; desktop exe paths last).
        names = {_norm(h["name"]) for h in hits}
        if len(names) == 1:
            hits.sort(key=lambda h: _PRIORITY.get(h["source"], 9))
            return dict(hits[0])
        # Genuinely different apps -> never guess-launch; ask instead.
        return {"candidates": [f"{h['name']} ({h['source']})" for h in hits]}
    return None
