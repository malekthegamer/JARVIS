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


def _resolve_executable(target: str) -> str | None:
    """Find a launchable path for `target`, or None. Checks, in order:
    a direct/existing path, PATH (with common exe extensions), and the
    Windows App Paths registry (where chrome/msedge/spotify/etc. register)."""
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
    return None


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
