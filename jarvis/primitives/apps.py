"""launch_app — the slice-2 action primitive (AUTO tier).

Resolution happens BEFORE anything spawns: an unknown name returns a clean
failure with nothing launched (never the blocking Windows "cannot find"
dialog). Voice-input mangling ("note pad", "notepad app") is normalized.
_resolve_executable + APP_ALIASES salvaged from legacy/skills/pc_control.py;
the WEBSITES map deliberately not carried over (web is a later slice).
"""
from __future__ import annotations

import os
import shutil
import subprocess

# Friendly name -> launch target (Windows). Extend freely.
APP_ALIASES = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe",
    "paint": "mspaint.exe", "explorer": "explorer.exe", "file explorer": "explorer.exe",
    "cmd": "cmd.exe", "terminal": "wt.exe", "powershell": "powershell.exe",
    "task manager": "taskmgr.exe", "settings": "ms-settings:",
    "chrome": "chrome", "edge": "msedge", "firefox": "firefox",
    "spotify": "spotify", "discord": "discord", "steam": "steam",
    "vscode": "code", "vs code": "code", "code": "code",
}


# Start Menu roots (user + all-users). Some installers — Spotify's per-user
# desktop build among them (slice-6 acceptance finding) — register NO App
# Paths key and aren't on PATH; their only launchable trace is a .lnk here.
_START_MENU_DIRS = [
    os.path.join(os.environ.get("APPDATA", ""),
                 r"Microsoft\Windows\Start Menu\Programs"),
    os.path.join(os.environ.get("PROGRAMDATA", ""),
                 r"Microsoft\Windows\Start Menu\Programs"),
]


def _lnk_target(lnk_path: str) -> str | None:
    """Resolve a .lnk shortcut to its target path (pywin32 ships with
    pywinauto, so WScript.Shell is always available here)."""
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        return shell.CreateShortcut(lnk_path).TargetPath or None
    except Exception:
        return None


def _resolve_shortcut(target: str) -> str | None:
    """Start Menu fallback: an EXACT-name '<target>.lnk' whose target exists.
    Exact match only — grabbing a similarly-named app and launching the wrong
    thing is worse than a clean failure (fail closed)."""
    want = target.lower()
    want = want[:-4] if want.endswith(".exe") else want
    for root_dir in _START_MENU_DIRS:
        if not os.path.isdir(root_dir):
            continue
        for dirpath, _dirs, files in os.walk(root_dir):
            for fn in files:
                if fn.lower() == want + ".lnk":
                    path = _lnk_target(os.path.join(dirpath, fn))
                    if path and os.path.isfile(path):
                        return path
    return None


def _resolve_executable(target: str) -> str | None:
    """Find a launchable path for `target`, or None. Checks, in order:
    a direct/existing path, PATH (with common exe extensions), the Windows
    App Paths registry, and finally an exact-name Start Menu shortcut."""
    if os.path.isfile(target):
        return target
    hit = shutil.which(target)
    if hit:
        return hit
    for ext in (".exe", ".com", ".bat"):
        hit = shutil.which(target + ext)
        if hit:
            return hit
    name = target if target.lower().endswith(".exe") else target + ".exe"
    try:
        import winreg
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}"
                with winreg.OpenKey(root, key_path) as key:
                    path, _ = winreg.QueryValueEx(key, None)
                    if path and os.path.isfile(path.strip('"')):
                        return path.strip('"')
            except FileNotFoundError:
                continue
    except Exception:
        pass
    return _resolve_shortcut(target)


def _candidates(name: str) -> list[str]:
    """Normalization ladder for voice-mangled names, most-literal first."""
    base = name.strip().lower()
    out = [base]
    if base.endswith(" app"):
        out.append(base[:-4].strip())
    out.extend(c.replace(" ", "") for c in list(out) if " " in c)
    seen: list[str] = []
    for c in out:
        if c and c not in seen:
            seen.append(c)
    return seen


def resolve_app(name: str) -> tuple[str | None, str]:
    """Return (launchable target, candidate that matched). The target is an
    executable path or a URI like 'ms-settings:'; None if nothing matched.

    Slice 22: after the fast ladder misses, fall back to real discovery —
    desktop shortcuts, Steam libraries, Epic manifests (app_discovery.find).
    The discovery scan only runs where today we'd return "not found", so its
    cost lands exclusively on the previously-failing path. An ambiguous
    discovery match launches nothing (candidates surface via launch_app)."""
    for candidate in _candidates(name):
        target = APP_ALIASES.get(candidate, candidate)
        if _is_uri(target):
            return target, candidate
        path = _resolve_executable(target)
        if path:
            return path, candidate
    from jarvis.primitives import app_discovery  # late: cheap import, clear seam
    hit = app_discovery.find(name)
    if hit and hit.get("launch"):
        return hit["launch"], hit["name"]
    return None, name


def _is_uri(target: str) -> bool:
    # colon but not a "C:\" drive path
    return ":" in target and not (len(target) > 1 and target[1] == ":")


# Sites people name by brand rather than domain. Deliberately SHORT — this is a
# convenience for the handful of things said out loud constantly, not a
# directory. An unlisted bare word is refused with an honest message, and the
# model simply supplies the real URL instead.
_SITE_ALIASES = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "reddit": "https://www.reddit.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "netflix": "https://www.netflix.com",
    "spotify": "https://open.spotify.com",
    "twitch": "https://www.twitch.tv",
    "whatsapp": "https://web.whatsapp.com",
    "drive": "https://drive.google.com",
    "maps": "https://maps.google.com",
    "amazon": "https://www.amazon.com",
    "x": "https://x.com",
    "twitter": "https://x.com",
}


def normalize_url(text: str) -> str | None:
    """A user's phrasing -> a real http(s) URL, or None if it isn't one.

    Accepts what people actually say/type: a full URL, a bare domain
    ("youtube.com"), or a well-known brand name ("youtube"). Returns None for
    anything else — crucially including FILE PATHS, because the caller hands the
    result to os.startfile, which would happily execute them.
    """
    s = str(text or "").strip().strip('"').strip("'")
    if not s:
        return None
    low = s.lower()

    if low in _SITE_ALIASES:
        return _SITE_ALIASES[low]

    if "://" in low:
        # Only the two web schemes may ever pass. file://, javascript:, ms-*:,
        # and friends are refused here rather than at the OS.
        if not (low.startswith("http://") or low.startswith("https://")):
            return None
        rest = s.split("://", 1)[1]
        return s if rest.strip() else None

    # A drive path (C:\...), a UNC share (\\host\...) or a bare executable is
    # NOT a website, however domain-ish it looks.
    if s.startswith("\\\\") or "\\" in s or (len(s) > 1 and s[1] == ":"):
        return None
    if ":" in s:                      # "cmd.exe:" / "ms-settings:" style
        return None
    if "." not in s.strip("."):
        return None                   # a bare word we have no alias for
    if s.lower().rsplit(".", 1)[-1] in ("exe", "bat", "cmd", "ps1", "msi",
                                        "com", "scr", "vbs", "lnk"):
        # ".com" is a real TLD AND an executable extension; a lone "foo.com"
        # is a site, but anything with a path separator was rejected above, so
        # what remains here reads as an executable name.
        if s.lower().endswith((".exe", ".bat", ".cmd", ".ps1", ".msi",
                               ".scr", ".vbs", ".lnk")):
            return None
    return "https://" + s


def open_url(url: str) -> dict:
    """Open a website in the user's REAL default browser. {ok, message}.

    SLICE 60, and the single highest-value reliability change in the project.
    Measured over 313 real actions, `browse_navigate` failed 24% of the time —
    because "open YouTube" was being routed through a browser-AUTOMATION stack
    (Playwright / CDP / an MV3 extension), any part of which can be
    misconfigured, and one of which opens a deliberately profile-less window.

    Opening a page needs none of that. Windows already knows the user's default
    browser and their signed-in profile; handing it the URL is one step with
    essentially nothing to break. This is the technique from the owner's
    previous JARVIS (`Start-Process <url>`), which never failed at this.

    Never raises. Only http/https ever reaches the OS — see normalize_url; this
    function must never become a way to execute a file.
    """
    target = normalize_url(url)
    if target is None:
        return {"ok": False, "resolved": None,
                "message": (f"'{url}' isn't a website I can open. Give me a full "
                            f"URL like https://example.com — I only open web "
                            f"pages this way, never files or programs.")}
    try:
        os.startfile(target)
    except Exception as exc:
        return {"ok": False, "resolved": target,
                "message": f"Couldn't open {target}: {exc}"}
    return {"ok": True, "resolved": target,
            "message": f"Opened {target} in your browser."}


def launch_app(name: str) -> dict:
    """AUTO tier. Returns {"ok", "message", "pid", "resolved"} — never raises."""
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "message": "No application name given.",
                "pid": None, "resolved": None}
    try:
        target, matched = resolve_app(name)
        if target is None:
            return {"ok": False, "pid": None, "resolved": None, "matched": None,
                    "message": f"No application named '{name}' found on this system."}
        if _is_uri(target):
            os.startfile(target)
            return {"ok": True, "pid": None, "resolved": target,
                    "matched": matched, "message": f"Opened {target}."}
        if os.path.isdir(target):
            # A folder shortcut: open in Explorer (no pid of our own to claim).
            os.startfile(target)
            return {"ok": True, "pid": None, "resolved": target,
                    "matched": matched,
                    "message": f"Opened folder {os.path.basename(target)} in Explorer."}
        proc = subprocess.Popen([target])
        return {"ok": True, "pid": proc.pid, "resolved": target,
                "matched": matched,
                "message": f"Launched {os.path.basename(target)} (pid {proc.pid})."}
    except Exception as exc:
        return {"ok": False, "pid": None, "resolved": None, "matched": None,
                "message": f"Couldn't launch '{name}': {exc}"}
