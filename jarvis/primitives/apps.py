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
    executable path or a URI like 'ms-settings:'; None if nothing matched."""
    for candidate in _candidates(name):
        target = APP_ALIASES.get(candidate, candidate)
        if _is_uri(target):
            return target, candidate
        path = _resolve_executable(target)
        if path:
            return path, candidate
    return None, name


def _is_uri(target: str) -> bool:
    # colon but not a "C:\" drive path
    return ":" in target and not (len(target) > 1 and target[1] == ":")


def launch_app(name: str) -> dict:
    """AUTO tier. Returns {"ok", "message", "pid", "resolved"} — never raises."""
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "message": "No application name given.",
                "pid": None, "resolved": None}
    try:
        target, _matched = resolve_app(name)
        if target is None:
            return {"ok": False, "pid": None, "resolved": None,
                    "message": f"No application named '{name}' found on this system."}
        if _is_uri(target):
            os.startfile(target)
            return {"ok": True, "pid": None, "resolved": target,
                    "message": f"Opened {target}."}
        proc = subprocess.Popen([target])
        return {"ok": True, "pid": proc.pid, "resolved": target,
                "message": f"Launched {os.path.basename(target)} (pid {proc.pid})."}
    except Exception as exc:
        return {"ok": False, "pid": None, "resolved": None,
                "message": f"Couldn't launch '{name}': {exc}"}
