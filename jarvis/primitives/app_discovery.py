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
import time as _time

# Module-level so tests (and odd setups) can repoint them.
EPIC_MANIFEST_DIR = r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests"
DESKTOP_DIRS = [
    os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
    r"C:\Users\Public\Desktop",
]

_STEAM_SKIP = {"steamworks common redistributables"}

# Start Menu scan cache: {dirs_tuple: (timestamp, entries)}. Short TTL so a
# freshly-installed app shows up without restarting JARVIS.
_SM_CACHE: dict[tuple, tuple[float, list]] = {}
_SM_CACHE_TTL_S = 60.0


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


# Roman numerals people actually say in game titles. SLICE 64: deliberately NO
# bare "i"/"v"/"x" — those are real names ("X", "Vim" tokenizes to "vim", a lone
# "V"), and turning them into numbers would corrupt far more than it fixes. The
# accepted cost: "gta v" does not normalize to "gta 5".
_ROMAN = {"ii": "2", "iii": "3", "iv": "4", "vi": "6", "vii": "7", "viii": "8",
          "ix": "9", "xi": "11", "xii": "12", "xiii": "13", "xiv": "14",
          "xv": "15", "xvi": "16", "xvii": "17", "xviii": "18", "xix": "19",
          "xx": "20"}


def start_menu_apps() -> list[dict]:
    """Every Start Menu .lnk whose target still exists.

    SLICE 64. apps._resolve_shortcut already walks these folders, but only for
    an EXACT '<name>.lnk' — so find()/suggest() could never fuzzy-match one.
    Measured on the owner's machine: 145 apps resolve by their exact name and
    NOT ONE of them could be offered as a suggestion after a near-miss. That is
    the slice-60 dead end ("No application named X found", model invents a
    name), still wide open for most of what's installed.

    Targets are validated the way desktop_shortcuts() validates them: a .lnk
    pointing at an uninstalled program is a leftover record, not an inventory —
    the same mistake the AppCompatFlags graveyard taught in slice 63.
    Measured cost of the walk: ~7 ms. Never raises.
    """
    from jarvis.primitives import apps  # late: avoid import cycle

    # Cached because resolving a .lnk is a COM round trip EACH, and there are
    # ~160 of them: I first measured only the os.walk (7 ms) and reported that
    # as the cost. The real figure is ~280 ms, and find() and suggest() both
    # call this, so an uncached miss paid it twice. Keyed on the directories so
    # tests that repoint _START_MENU_DIRS never hit a stale entry.
    key = tuple(apps._START_MENU_DIRS)
    hit = _SM_CACHE.get(key)
    if hit and (_time.time() - hit[0]) < _SM_CACHE_TTL_S:
        return list(hit[1])

    out: list[dict] = []
    seen: set[str] = set()
    for root_dir in apps._START_MENU_DIRS:
        try:
            if not os.path.isdir(root_dir):
                continue
            for dirpath, _dirs, files in os.walk(root_dir):
                for fn in files:
                    if not fn.lower().endswith(".lnk"):
                        continue
                    target = apps._lnk_target(os.path.join(dirpath, fn))
                    if not target or not (os.path.isfile(target)
                                          or os.path.isdir(target)):
                        continue
                    dedupe = target.casefold()
                    if dedupe in seen:
                        continue
                    seen.add(dedupe)
                    out.append({"name": os.path.splitext(fn)[0],
                                "source": "start_menu", "launch": target})
        except Exception:
            continue
    _SM_CACHE[key] = (_time.time(), list(out))
    return out


def _norm(text: str) -> str:
    """Casefold; every non-alphanumeric (®, ™, dashes, dots) becomes a space
    — probe-driven: Epic's DisplayName is literally 'Rocket League®'.

    SLICE 64 adds two things, each from a measured miss on the owner's machine:
    a digit welded to a word is split ('Spider-Man2' -> 'spider man 2', which is
    why a spoken "spider-man 2" never matched), and multi-letter roman numerals
    become arabic ('Black Ops II' -> 'black ops 2').
    """
    s = "".join(c if c.isalnum() else " " for c in str(text or "").casefold())
    out = []
    for token in s.split():
        # "man2" -> "man 2", "2fort" -> "2 fort"
        for part in re.findall(r"\d+|[^\W\d]+", token, flags=re.UNICODE):
            out.append(_ROMAN.get(part, part))
    return " ".join(out)


def _flat(text: str) -> str:
    """Normalized with spaces removed — 'prismlauncher' vs 'Prism Launcher'."""
    return _norm(text).replace(" ", "")


# Words that mark an entry as a program's accessory rather than the program:
# its settings, its uninstaller, a patch shortcut. Matched as whole normalized
# tokens so "Setup" is caught but "Setups Inc" isn't.
_AUXILIARY = frozenset({"settings", "config", "configuration", "uninstall",
                        "uninstaller", "readme", "setup", "update", "updater",
                        "documentation", "docs", "support", "help", "manual",
                        "crash", "report", "benchmark", "editor", "server"})


def _is_auxiliary(name: str) -> bool:
    return bool(_AUXILIARY & set(_norm(name).split()))


def _without_auxiliary(entries: list[dict], needle: str) -> list[dict]:
    """Drop accessory entries — unless the user's own words asked for one, and
    unless dropping would leave nothing (then the accessory IS the answer)."""
    if _AUXILIARY & set(needle.split()):
        return entries
    kept = [e for e in entries if not _is_auxiliary(e.get("name", ""))]
    return kept or entries


def suggest(name: str, limit: int = 5) -> list[str]:
    """Closest installed app names for a lookup that MISSED. Never raises.

    SLICE 60. Ambiguity already returned candidates, but a miss returned a flat
    "No application named 'X' found", which gives the model nothing to recover
    with — so it either gave up or invented a name. Both are in the owner's
    audit log, back to back:

        FAILED: No application named 'Rocket League' found on this system.
        FAILED: No application named 'Rocket Leaguer.url' found on this system.

    The second is what inventing looks like. Offering real names turns a dead
    end into a retry the model can actually get right.
    """
    try:
        import difflib

        entries = (desktop_shortcuts() + steam_games() + epic_games()
                   + start_menu_apps())
        names = sorted({str(e.get("name") or "").strip()
                        for e in entries if e.get("name")})
        if not names:
            return []
        needle = _norm(name)
        # difflib first — it handles typos and mangled suffixes ("Leaguer.url").
        close = difflib.get_close_matches(needle, [_norm(n) for n in names],
                                          n=limit, cutoff=0.6)
        by_norm = {_norm(n): n for n in names}
        out = [by_norm[c] for c in close if c in by_norm]
        # Then token overlap, which catches abbreviations difflib misses
        # ("vs code" -> "Visual Studio Code").
        if len(out) < limit:
            want = set(needle.split())
            scored = sorted(
                ((len(want & set(_norm(n).split())), n) for n in names),
                key=lambda t: (-t[0], t[1]))
            for score, n in scored:
                if score and n not in out:
                    out.append(n)
                if len(out) >= limit:
                    break
        return out[:limit]
    except Exception:
        return []


def find(name: str) -> dict | None:
    """Unique match across all sources, or candidates, or None.
    Precedence: exact normalized name > unique prefix > unique substring.
    >1 match at the winning tier => {"candidates": [...]} and NO launch."""
    needle = _norm(name)
    if not needle:
        return None
    entries = (desktop_shortcuts() + steam_games() + epic_games()
               + start_menu_apps())
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

    # SLICE 64: a game's own config/uninstall shortcut is not a rival game.
    # "fifa" matched both 'FIFA 22' and 'FIFA 22 Settings' and so asked a
    # question with only one real answer. Dropped UNLESS the user said the word
    # themselves, so "fifa settings" still finds it.
    uniq = _without_auxiliary(uniq, needle)

    _PRIORITY = {"steam": 0, "epic": 1, "desktop": 2, "start_menu": 3}
    flat_needle = needle.replace(" ", "")
    want_tokens = set(needle.split())
    # Tiers run most-literal first; a looser tier is only consulted when every
    # stricter one found nothing, so loosening cannot outrank an exact match.
    for tier in ("exact", "prefix", "substring", "spaceless", "tokens"):
        hits = []
        for e in uniq:
            n = _norm(e["name"])
            if ((tier == "exact" and n == needle)
                    or (tier == "prefix" and n.startswith(needle))
                    or (tier == "substring" and needle in n)
                    or (tier == "spaceless" and flat_needle
                        and flat_needle in n.replace(" ", ""))
                    # "fifa settings" -> "FIFA 22 Settings": every word the user
                    # said is present, just not contiguously.
                    or (tier == "tokens" and want_tokens
                        and want_tokens <= set(n.split()))):
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
