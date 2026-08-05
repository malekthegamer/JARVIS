"""launch_app — the slice-2 action primitive (AUTO tier).

Resolution happens BEFORE anything spawns: an unknown name returns a clean
failure with nothing launched (never the blocking Windows "cannot find"
dialog). Voice-input mangling ("note pad", "notepad app") is normalized.
_resolve_executable + APP_ALIASES salvaged from legacy/skills/pc_control.py;
the WEBSITES map deliberately not carried over (web is a later slice).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

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


def resolve_app_detail(name: str) -> tuple[str | None, str, list[str]]:
    """resolve_app, plus the candidate list it throws away.

    SLICE 64: when discovery found SEVERAL matches, resolve_app returned a bare
    None and the names died here — so launch_app reported "No application named
    'resident evil' found" when it had in fact found three. Same class of lie as
    slice 63's "doesn't appear to be installed".

    Deliberately a thin wrapper AROUND resolve_app rather than a replacement for
    its body: resolve_app is the seam tests stub, and moving the real work out
    from under them would have silently stopped exercising the code they name.
    The second find() call only happens once resolution has already failed.
    """
    target, matched = resolve_app(name)
    if target is not None:
        return target, matched, []
    from jarvis.primitives import app_discovery  # late: cheap import, clear seam
    hit = app_discovery.find(name)
    if hit and hit.get("candidates"):
        return None, name, list(hit["candidates"])
    return None, name, []


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


# os.startfile RUNS these rather than displaying them. launch_app and run_shell
# are the deliberate, gated routes to execution; open_path opens documents and
# must never become a quiet way to execute something — the same reasoning that
# makes open_url refuse file paths entirely.
_EXECUTABLE_SUFFIXES = frozenset({
    ".exe", ".bat", ".cmd", ".ps1", ".psm1", ".msi", ".scr", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".com", ".pif", ".cpl", ".hta", ".reg",
})


def is_executable_path(path) -> bool:
    """Would opening this RUN something? Never raises."""
    try:
        return Path(str(path)).suffix.lower() in _EXECUTABLE_SUFFIXES
    except Exception:
        return True          # unreadable -> treat as dangerous


def open_path(path: str) -> dict:
    """Open a local file or folder in the user's default app. {ok, message}.

    SLICE 62. Every real-use `click` failure in the audit log was the model
    trying to open a file by double-clicking its DESKTOP ICON — because there
    was no verb for this, so its only route was the most fragile path it owns:
    capture the desktop, ask a vision model to locate the icon, verify the
    point, then `kind='double'` (the known-flaky kind). A file has a default
    handler; Windows already knows it.

    Directly parallel to slice 60's open_url, which replaced driving a browser
    stack with one OS handoff and has not failed since.

    Never raises. An executable is REFUSED — see _EXECUTABLE_SUFFIXES.
    """
    raw = str(path or "").strip().strip('"').strip("'")
    if not raw:
        return {"ok": False, "resolved": None,
                "message": "Give me the path of a file or folder to open."}
    try:
        from jarvis.primitives.fsaccess import resolve_user_path
        target = resolve_user_path(raw)
        if target is None:
            return {"ok": False, "resolved": None,
                    "message": f"Couldn't understand the path '{raw}'."}
        if is_executable_path(target):
            return {"ok": False, "resolved": str(target),
                    "message": (f"'{target.name}' is a program, and opening it "
                                f"would RUN it. Use launch_app to start an app, "
                                f"or run_shell if you really mean to execute "
                                f"something — both ask you first.")}
        if not target.exists():
            return {"ok": False, "resolved": str(target),
                    "message": f"There's nothing at '{target}' to open."}
        os.startfile(str(target))
    except Exception as exc:
        return {"ok": False, "resolved": raw,
                "message": f"Couldn't open '{raw}': {exc}"}
    kind = "folder" if target.is_dir() else "file"
    return {"ok": True, "resolved": str(target),
            "message": f"Opened the {kind} '{target.name}'."}


# Windows error codes we act on by name rather than by magic number.
_ERROR_ELEVATION_REQUIRED = 740   # CreateProcess refused; the target needs admin
_ERROR_CANCELLED = 1223           # the user clicked No on the UAC prompt

# ShellExecute BLOCKS until the UAC prompt is answered. Measured in the slice-63
# probe: 10.1s for a click, and past 120s when the prompt was simply ignored.
# Inside the executor that would freeze the whole chain, so the wait is bounded.
_UAC_WAIT_S = 90.0


def _shell_launch(target: str, work_dir: str | None, matched: str) -> dict:
    """Launch via ShellExecute (os.startfile), which is the ONLY way to start a
    program that requires elevation — by manifest or by the RUNASADMIN
    compatibility flag. Windows shows its own consent prompt on the secure
    desktop; we cannot and must not try to answer it.

    Run on a worker thread so an unanswered prompt costs a bounded wait instead
    of hanging the chain. No pid is available from ShellExecute, so none is
    claimed — reporting a fake one would be worse than reporting none.
    """
    import threading

    base = os.path.basename(target)
    outcome: dict = {}

    def run():
        try:
            os.startfile(target, cwd=work_dir)
            outcome["ok"] = True
        except OSError as exc:
            outcome["ok"] = False
            outcome["exc"] = exc

    worker = threading.Thread(target=run, daemon=True, name="uac-launch")
    worker.start()
    worker.join(_UAC_WAIT_S)

    if worker.is_alive():
        # The prompt is still on screen. Saying it failed would be a lie — it
        # may yet start the moment the user clicks.
        return {"ok": False, "pid": None, "resolved": target, "matched": matched,
                "message": (f"{base} needs administrator permission. Windows is "
                            f"still showing the approval prompt — approve it and "
                            f"{base} will start.")}
    if outcome.get("ok"):
        return {"ok": True, "pid": None, "resolved": target, "matched": matched,
                "message": f"Launched {base} as administrator."}

    exc = outcome.get("exc")
    if getattr(exc, "winerror", None) == _ERROR_CANCELLED:
        return {"ok": False, "pid": None, "resolved": target, "matched": matched,
                "declined": True,   # slice 67: a choice, not a breakage
                "message": (f"{base} needs administrator permission and the "
                            f"Windows prompt was declined, so it didn't start.")}
    return {"ok": False, "pid": None, "resolved": target, "matched": matched,
            "message": f"Couldn't launch {base}: {exc}"}


def launch_app(name: str) -> dict:
    """AUTO tier. Returns {"ok", "message", "pid", "resolved"} — never raises."""
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "message": "No application name given.",
                "pid": None, "resolved": None}
    try:
        target, matched, choices = resolve_app_detail(name)
        if target is None and choices:
            # It found several. Saying "no application named X" here was simply
            # false, and it sent the model off inventing other names.
            # find() tags candidates with their source ("Resident Evil 2
            # (desktop)"). Useful for debugging, noise when it's SPOKEN aloud.
            listed = ", ".join(re.sub(r"\s*\((?:desktop|steam|epic|start_menu)\)$",
                                      "", c) for c in choices[:6])
            return {"ok": False, "pid": None, "resolved": None, "matched": None,
                    "candidates": choices,
                    "message": (f"Several things match '{name}' — which did you "
                                f"mean? {listed}")}
        if target is None:
            # SLICE 60: a miss used to dead-end here. The model then either gave
            # up or invented a name — both are in the audit log back to back
            # ('Rocket League', then 'Rocket Leaguer.url'). Offer what is
            # actually installed so the next round can succeed.
            from jarvis.primitives import app_discovery
            near = app_discovery.suggest(name)
            hint = (f" Did you mean: {', '.join(near)}?" if near else
                    " Try the exact name as it appears on the Start Menu, or "
                    "open_url if it is a website.")
            return {"ok": False, "pid": None, "resolved": None, "matched": None,
                    "candidates": near,
                    "message": f"No application named '{name}' found.{hint}"}
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
        # SLICE 63: cwd is the executable's OWN folder. Games routinely fail or
        # crash when started from elsewhere, and until now they inherited
        # JARVIS's working directory.
        work_dir = os.path.dirname(target) or None
        try:
            proc = subprocess.Popen([target], cwd=work_dir)
        except OSError as exc:
            if getattr(exc, "winerror", None) != _ERROR_ELEVATION_REQUIRED:
                raise
            # Popen -> CreateProcess, which CANNOT elevate; it only fails. This
            # is why "most games" wouldn't start: 26 of this machine's shortcuts
            # carry the RUNASADMIN compatibility flag, which only ShellExecute
            # honours. Measured: Popen 740 instantly, os.startfile OK in 10.1s.
            return _shell_launch(target, work_dir, matched)
        return {"ok": True, "pid": proc.pid, "resolved": target,
                "matched": matched,
                "message": f"Launched {os.path.basename(target)} (pid {proc.pid})."}
    except Exception as exc:
        return {"ok": False, "pid": None, "resolved": None, "matched": None,
                "message": f"Couldn't launch '{name}': {exc}"}
