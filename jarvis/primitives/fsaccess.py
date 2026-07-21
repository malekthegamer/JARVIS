"""Real-filesystem access (slice 32) — browse / delete-to-Recycle-Bin / make a
shortcut, ANYWHERE on the PC, not just the data/agent_files sandbox.

This INVERTS the workspace cage (files.py): broad access instead of an
allowlist. So the safety model is different and layered:

  * The CONFIRM gate is the boundary. Every mutation is confirmation-gated on
    the VERBATIM resolved absolute path (the user is the gate).
  * The denylist is a BACKSTOP, not the boundary (the run_shell doctrine): a
    handful of catastrophic targets — the Windows tree, Program Files /
    ProgramData / C:/Users roots, drive roots, the user profile root, and
    JARVIS's own dirs — are BLOCKED outright. It can never be exhaustive; the
    CONFIRM path is what actually protects the user.
  * Deletes go to the RECYCLE BIN (SHFileOperation + FOF_ALLOWUNDO), never a
    permanent unlink — a mistake is recoverable through normal Windows.

Classification runs on the RESOLVED path (.resolve() follows `..` and
symlinks), so a traversal or symlink can't smuggle a target past the denylist.

No new dependencies — Recycle Bin, shortcut creation, and known-folder
resolution all use pywin32 (already present). Every function returns
{ok, message, ...} and never raises (house style).
"""
from __future__ import annotations

import os
from pathlib import Path

from jarvis import config

LIST_CAP = 200                 # max entries returned by list_directory


# ------------------------------------------------------------------ resolution

def _known_folder(csidl: int, fallback: str) -> Path:
    try:
        from win32com.shell import shell, shellcon  # lazy
        return Path(shell.SHGetFolderPath(0, csidl, None, 0))
    except Exception:
        return Path(os.path.expandvars(fallback))


def _build_known_folders() -> dict[str, Path]:
    prof = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    try:
        from win32com.shell import shellcon
        return {
            "desktop": _known_folder(shellcon.CSIDL_DESKTOPDIRECTORY, prof + r"\Desktop"),
            "documents": _known_folder(shellcon.CSIDL_PERSONAL, prof + r"\Documents"),
            "downloads": Path(prof) / "Downloads",   # no stable CSIDL; convention
            "pictures": _known_folder(shellcon.CSIDL_MYPICTURES, prof + r"\Pictures"),
            "music": _known_folder(shellcon.CSIDL_MYMUSIC, prof + r"\Music"),
            "videos": Path(prof) / "Videos",
            "home": Path(prof),
        }
    except Exception:
        return {"desktop": Path(prof) / "Desktop", "documents": Path(prof) / "Documents",
                "downloads": Path(prof) / "Downloads", "pictures": Path(prof) / "Pictures",
                "music": Path(prof) / "Music", "videos": Path(prof) / "Videos",
                "home": Path(prof)}


# Module-level so tests can repoint an alias at a tmp dir.
KNOWN_FOLDERS: dict[str, Path] = _build_known_folders()


def resolve_user_path(raw: str) -> Path | None:
    """Resolve a user-supplied path to an absolute, symlink-followed Path, or
    None. Handles a leading known-folder alias ('desktop', 'downloads', …),
    env vars (%USERPROFILE%), ~ , and relative paths. Never raises."""
    raw = str(raw or "").strip().strip('"').strip("'")
    if not raw:
        return None
    try:
        # leading alias: "desktop", "desktop/games", "downloads\\x"
        head, _, rest = raw.replace("\\", "/").partition("/")
        base = KNOWN_FOLDERS.get(head.lower())
        if base is not None:
            p = base / rest if rest else base
        else:
            p = Path(os.path.expandvars(os.path.expanduser(raw)))
        return p.resolve()
    except Exception:
        return None


# ------------------------------------------------------------------ denylist

def _anchors() -> list[Path]:
    """Protected roots, resolved. Deleting/writing at (or above) any of these
    is refused outright. Rebuilt each call so tests/env changes are honoured."""
    env = os.environ.get
    cands = [
        env("SystemRoot", r"C:\Windows"),
        env("ProgramFiles", r"C:\Program Files"),
        env("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        env("ProgramData", r"C:\ProgramData"),
        r"C:\Users",
        env("USERPROFILE", r"C:\Users\Default"),
    ]
    out = []
    for c in cands:
        try:
            out.append(Path(c).resolve())
        except Exception:
            continue
    for d in (config.BASE_DIR, config.DATA_DIR):
        try:
            out.append(Path(d).resolve())
        except Exception:
            pass
    return out


def _within(p: Path, root: Path) -> bool:
    try:
        return p == root or p.is_relative_to(root)   # WindowsPath compares case-insensitively
    except Exception:
        return False


def classify_path_risk(p: Path) -> tuple[str, str]:
    """('blocked'|'confirm', reason) for a RESOLVED path. The Windows tree and
    JARVIS's own dirs block the target AND all descendants; other anchors block
    the anchor itself and any ancestor of it (deleting C:\\ or C:\\Users)."""
    try:
        p = Path(p).resolve()
    except Exception:
        return "confirm", "unresolvable path — confirming"

    systemroot = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()
    # (1) hard trees — nothing under these is ever a legit target
    for tree in (systemroot, Path(config.BASE_DIR).resolve(), Path(config.DATA_DIR).resolve()):
        if _within(p, tree):
            return "blocked", f"'{p}' is inside a protected system/app area."
    # (2) a drive root itself (C:\, D:\ …) — no relative part
    if p.parent == p:
        return "blocked", f"'{p}' is a drive root."
    # (3) a protected anchor, or an ancestor of one (deleting it takes the anchor)
    for a in _anchors():
        if p == a or _within(a, p):
            return "blocked", f"'{p}' is (or contains) a protected system location."
    return "confirm", f"'{p}'"


# ------------------------------------------------------------------ OS seams

def _recycle(path: Path) -> None:
    """Send a file/folder to the Recycle Bin (recoverable). Raises on failure
    (the caller turns it into an honest {ok:False})."""
    from win32com.shell import shell, shellcon  # lazy
    flags = (shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION
             | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI)
    # pFrom must be double-null terminated.
    rc, aborted = shell.SHFileOperation(
        (0, shellcon.FO_DELETE, str(path) + "\0\0", None, flags, None, None))
    if rc != 0 or aborted:
        raise OSError(f"SHFileOperation failed (code {rc}, aborted={aborted})")


def _create_lnk(dest: Path, target: Path) -> None:
    """Create a .lnk at `dest` pointing at `target`. Raises on failure."""
    import win32com.client  # lazy
    ws = win32com.client.Dispatch("WScript.Shell")
    sc = ws.CreateShortcut(str(dest))
    sc.TargetPath = str(target)
    wd = target if target.is_dir() else target.parent
    sc.WorkingDirectory = str(wd)
    sc.Save()


# ------------------------------------------------------------------ verbs

def list_directory(path: str) -> dict:
    """Read-only browse of any folder (AUTO). {ok, entries, message}."""
    p = resolve_user_path(path)
    if p is None:
        return {"ok": False, "entries": [], "message": f"Couldn't understand the path '{path}'."}
    if not p.exists():
        return {"ok": False, "entries": [], "message": f"No folder at '{p}'."}
    if not p.is_dir():
        return {"ok": False, "entries": [], "message": f"'{p}' is a file, not a folder."}
    entries: list[dict] = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if len(entries) >= LIST_CAP:
                break
            try:
                is_dir = child.is_dir()
                size = 0 if is_dir else child.stat().st_size
            except OSError:
                is_dir, size = False, 0
            entries.append({"name": child.name, "type": "dir" if is_dir else "file",
                            "size": size})
    except PermissionError:
        return {"ok": False, "entries": [], "message": f"Access denied listing '{p}'."}
    except Exception as exc:
        return {"ok": False, "entries": [], "message": f"Couldn't list '{p}': {exc}"}
    listing = "; ".join(f"{e['name']}{'/' if e['type']=='dir' else ''}" for e in entries)
    more = "" if len(entries) < LIST_CAP else f" (showing the first {LIST_CAP})"
    return {"ok": True, "entries": entries,
            "message": f"'{p}' contains {len(entries)} item(s){more}: {listing}"}


def delete_path(path: str) -> dict:
    """Move a file/folder to the Recycle Bin. CONFIRM/BLOCKED tier is decided by
    classify_delete_path; this runs only after approval. {ok, message}."""
    p = resolve_user_path(path)
    if p is None:
        return {"ok": False, "message": f"Couldn't understand the path '{path}'."}
    if not p.exists():
        return {"ok": False, "message": f"There's no file or folder at '{p}'."}
    try:
        _recycle(p)
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't delete '{p}': {exc}"}
    kind = "folder" if p.is_dir() else "file"
    return {"ok": True,
            "message": f"Moved the {kind} '{p}' to the Recycle Bin — you can "
                       f"restore it from there if needed."}


def create_shortcut(target: str, name: str = "", location: str = "desktop") -> dict:
    """Create a .lnk (default on the Desktop) pointing at `target`. {ok, message}."""
    tgt = resolve_user_path(target)
    if tgt is None:
        return {"ok": False, "message": f"Couldn't understand the target '{target}'."}
    loc = resolve_user_path(location or "desktop")
    if loc is None:
        return {"ok": False, "message": f"Couldn't understand where to put it ('{location}')."}
    stem = (str(name).strip() or (tgt.name or "shortcut"))
    if stem.lower().endswith(".lnk"):
        stem = stem[:-4]
    dest = loc / f"{stem}.lnk"
    try:
        _create_lnk(dest, tgt)
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't create the shortcut: {exc}"}
    warn = "" if tgt.exists() else " (note: the target doesn't currently exist)"
    return {"ok": True,
            "message": f"Created a shortcut to '{tgt}' at '{dest}'.{warn}"}


# ------------------------------------------------------------------ classifiers

def classify_delete_path(args: dict) -> dict:
    """BLOCKED for catastrophic targets, else CONFIRM with the verbatim resolved
    path in the modal's mono box. Fail-closed to confirm. Never raises."""
    raw = str(args.get("path", ""))
    try:
        p = resolve_user_path(raw)
        if p is None:
            return {"tier": "confirm", "command": raw,
                    "description": f"Delete '{raw}' (couldn't resolve it — confirming)."}
        level, why = classify_path_risk(p)
        if level == "blocked":
            return {"tier": "blocked",
                    "description": f"BLOCKED: I won't delete that — {why} "
                                   f"Deleting it could break Windows or your account."}
        return {"tier": "confirm", "command": str(p),
                "description": f"Delete this from your PC (it goes to the Recycle Bin): {p}"}
    except Exception:
        return {"tier": "confirm", "command": raw,
                "description": f"Delete '{raw}' (couldn't classify — confirming)."}


def classify_create_shortcut(args: dict) -> dict:
    """The DESTINATION dir is what gets written, so classify that. BLOCKED into a
    protected dir, else CONFIRM naming target + destination. Never raises."""
    target = str(args.get("target", ""))
    location = str(args.get("location", "") or "desktop")
    try:
        loc = resolve_user_path(location)
        if loc is not None:
            level, why = classify_path_risk(loc)
            if level == "blocked":
                return {"tier": "blocked",
                        "description": f"BLOCKED: I won't create a shortcut there — {why}"}
        tgt = resolve_user_path(target)
        where = str(loc) if loc is not None else location
        return {"tier": "confirm", "command": f"{tgt or target}  ->  {where}",
                "description": f"Create a shortcut to '{tgt or target}' in '{where}'."}
    except Exception:
        return {"tier": "confirm",
                "description": "Create a shortcut (couldn't classify — confirming)."}
