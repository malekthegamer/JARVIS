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
import shutil
from pathlib import Path

from jarvis import config

LIST_CAP = 200                 # max entries returned by list_directory
READ_MAX_CHARS = 256 * 1024    # cap read_path so a huge file can't blow up context


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


def _mkdir(p: Path) -> None:
    """The OS seam, isolated so tests can prove a BLOCKED call never reaches
    the filesystem at all (rather than merely reporting a failure afterwards)."""
    p.mkdir(parents=True, exist_ok=True)


def make_folder(path: str) -> dict:
    """Create a directory (with any missing parents) ANYWHERE on the PC.
    {ok, message}. Never raises.

    Slice 56. The fs verbs could write/read/move/rename/copy/delete/shortcut
    anywhere but could not MAKE a folder, so JARVIS could put files on the
    Desktop and never organise them.

    An existing directory is a SUCCESS, not an error: the caller asked for the
    folder to exist and it does. An existing FILE at that path is a failure —
    silently succeeding there would imply a folder that isn't one.

    Deliberately NOT undoable: removing a just-created folder is only safe while
    it is empty, and undo promises restoring a previous state, not deleting.
    """
    p = resolve_user_path(path)
    if p is None:
        return {"ok": False, "message": f"Couldn't understand the path '{path}'."}
    if p.is_file():
        return {"ok": False,
                "message": f"There's already a file at '{p}' — not replacing it."}
    if p.is_dir():
        return {"ok": True, "message": f"'{p}' already exists."}
    try:
        _mkdir(p)
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't create '{p}': {exc}"}
    return {"ok": True, "message": f"Created the folder '{p}'."}


def classify_make_folder(args: dict) -> dict:
    """BLOCKED into a protected tree, else CONFIRM on the verbatim resolved
    path. Same shape as its siblings; never raises, fails closed to CONFIRM."""
    path = str(args.get("path", ""))
    try:
        p = resolve_user_path(path)
        if p is not None:
            level, why = classify_path_risk(p)
            if level == "blocked":
                return {"tier": "blocked",
                        "description": f"BLOCKED: I won't create a folder there — {why}"}
            return {"tier": "confirm", "command": str(p),
                    "description": f"Create the folder '{p}'."}
        return {"tier": "confirm",
                "description": f"Create the folder '{path}' (couldn't resolve — confirming)."}
    except Exception:
        return {"tier": "confirm",
                "description": "Create a folder (couldn't classify — confirming)."}


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


# ================================================================== slice 33:
# authoring verbs — write / read / move / rename / copy anywhere. Same proven
# core (resolve_user_path + classify_path_risk); every overwrite/clobber
# recycles the prior version first so it stays recoverable (delete parity).

def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _move(src: Path, dest: Path) -> None:
    shutil.move(str(src), str(dest))


def _copy(src: Path, dest: Path) -> None:
    if Path(src).is_dir():
        shutil.copytree(str(src), str(dest))
    else:
        shutil.copy2(str(src), str(dest))


def _max_write_kb() -> int:
    from jarvis.core.settings_store import settings
    try:
        return max(1, int(settings.get("fs.max_write_kb", 256)))
    except (TypeError, ValueError):
        return 256


def write_path(path: str, content) -> dict:
    """Write/create a UTF-8 text file anywhere. An overwrite recycles the prior
    file first (recoverable). Creates parent dirs. Never raises."""
    p = resolve_user_path(path)
    if p is None:
        return {"ok": False, "message": f"Couldn't understand the path '{path}'."}
    if p.exists() and p.is_dir():
        return {"ok": False, "message": f"'{p}' is a folder — I can't write over it."}
    content = "" if content is None else str(content)
    if len(content.encode("utf-8")) > _max_write_kb() * 1024:
        return {"ok": False,
                "message": f"That content is too large to write (> {_max_write_kb()} KB)."}
    try:
        replaced = p.exists()
        if replaced:
            _recycle(p)                 # old version -> Recycle Bin (recoverable)
        p.parent.mkdir(parents=True, exist_ok=True)
        _write_text(p, content)
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't write '{p}': {exc}"}
    verb = "Replaced" if replaced else "Wrote"
    tail = " (the previous version is in the Recycle Bin)" if replaced else ""
    return {"ok": True, "message": f"{verb} '{p}' ({len(content)} chars){tail}."}


def read_path(path: str) -> dict:
    """Read a UTF-8 text file's content anywhere (AUTO). Size-capped. {ok,
    content, message}. Never raises."""
    p = resolve_user_path(path)
    if p is None:
        return {"ok": False, "content": "", "message": f"Couldn't understand the path '{path}'."}
    if not p.exists():
        return {"ok": False, "content": "", "message": f"There's no file at '{p}'."}
    if p.is_dir():
        return {"ok": False, "content": "",
                "message": f"'{p}' is a folder — use list_directory to browse it."}
    try:
        raw = _read_text(p)
    except Exception as exc:
        return {"ok": False, "content": "", "message": f"Couldn't read '{p}': {exc}"}
    if len(raw) > READ_MAX_CHARS:
        return {"ok": True, "content": raw[:READ_MAX_CHARS],
                "message": f"Read '{p}' (truncated to {READ_MAX_CHARS} of {len(raw)} chars)."}
    return {"ok": True, "content": raw, "message": f"Read '{p}' ({len(raw)} chars)."}


def _place(src: Path, dest: Path, mover) -> dict:
    """Shared move/copy landing logic: dest-is-dir -> go inside it; an existing
    FILE at the effective path is recycled first (recoverable); an existing
    FOLDER is refused (no silent merge). Returns {ok, eff} / {ok:False}."""
    eff = (dest / src.name) if (dest.exists() and dest.is_dir()) else dest
    if eff.exists():
        if eff.is_dir():
            return {"ok": False,
                    "message": f"a folder already exists at '{eff}' — I won't replace it."}
        _recycle(eff)
    eff.parent.mkdir(parents=True, exist_ok=True)
    mover(src, eff)
    return {"ok": True, "eff": eff}


def move_path(source: str, dest: str) -> dict:
    """Move (or rename) a file/folder. Never raises."""
    s = resolve_user_path(source)
    d = resolve_user_path(dest)
    if s is None or d is None:
        return {"ok": False, "message": f"Couldn't understand the paths ('{source}' -> '{dest}')."}
    if not s.exists():
        return {"ok": False, "message": f"There's nothing to move at '{s}'."}
    try:
        r = _place(s, d, _move)
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't move '{s}': {exc}"}
    if not r["ok"]:
        return r
    return {"ok": True, "message": f"Moved '{s}' to '{r['eff']}'."}


def rename_path(path: str, new_name: str) -> dict:
    """Rename in place (a move within the same folder). Never raises."""
    new_name = str(new_name or "").strip()
    if not new_name or any(sep in new_name for sep in ("/", "\\")) or ".." in new_name:
        return {"ok": False,
                "message": "Give me just a new name (not a path) to rename to."}
    p = resolve_user_path(path)
    if p is None:
        return {"ok": False, "message": f"Couldn't understand the path '{path}'."}
    if not p.exists():
        return {"ok": False, "message": f"There's nothing to rename at '{p}'."}
    try:
        r = _place(p, p.parent / new_name, _move)
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't rename '{p}': {exc}"}
    if not r["ok"]:
        return r
    return {"ok": True, "message": f"Renamed '{p}' to '{r['eff']}'."}


def copy_path(source: str, dest: str) -> dict:
    """Copy a file/folder (the source stays). Never raises."""
    s = resolve_user_path(source)
    d = resolve_user_path(dest)
    if s is None or d is None:
        return {"ok": False, "message": f"Couldn't understand the paths ('{source}' -> '{dest}')."}
    if not s.exists():
        return {"ok": False, "message": f"There's nothing to copy at '{s}'."}
    try:
        r = _place(s, d, _copy)
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't copy '{s}': {exc}"}
    if not r["ok"]:
        return r
    return {"ok": True, "message": f"Copied '{s}' to '{r['eff']}'."}


# ---- classifiers (mirror classify_delete_path; verbatim path(s) in `command`) ----

def _confirm_or_block_path(raw: str, action: str, extra_block: str = "") -> dict | None:
    """None => the path is fine (caller builds the confirm); a dict => BLOCKED."""
    p = resolve_user_path(raw)
    if p is None:
        return None
    level, why = classify_path_risk(p)
    if level == "blocked":
        return {"tier": "blocked",
                "description": f"BLOCKED: I won't {action} — {why}{extra_block}"}
    return None


def classify_write_path(args: dict) -> dict:
    raw = str(args.get("path", ""))
    try:
        blocked = _confirm_or_block_path(raw, "write there")
        if blocked:
            return blocked
        p = resolve_user_path(raw)
        if p is None:
            return {"tier": "confirm", "command": raw,
                    "description": f"Write '{raw}' (couldn't resolve — confirming)."}
        verb = "Overwrite" if p.exists() else "Create"
        note = " (the current version goes to the Recycle Bin)" if p.exists() else ""
        return {"tier": "confirm", "command": str(p),
                "description": f"{verb} this file on your PC{note}: {p}"}
    except Exception:
        return {"tier": "confirm", "command": raw,
                "description": f"Write '{raw}' (couldn't classify — confirming)."}


def classify_move_path(args: dict) -> dict:
    src = str(args.get("source", ""))
    dst = str(args.get("dest", ""))
    try:
        for raw, act in ((src, "move that"), (dst, "move it there")):
            b = _confirm_or_block_path(raw, act)
            if b:
                return b
        s, d = resolve_user_path(src), resolve_user_path(dst)
        return {"tier": "confirm", "command": f"{s or src}  ->  {d or dst}",
                "description": f"Move '{s or src}' to '{d or dst}'."}
    except Exception:
        return {"tier": "confirm", "description": "Move a file (couldn't classify — confirming)."}


def classify_rename_path(args: dict) -> dict:
    path = str(args.get("path", ""))
    new_name = str(args.get("new_name", ""))
    try:
        p = resolve_user_path(path)
        if p is not None:
            level, why = classify_path_risk(p)
            if level == "blocked":
                return {"tier": "blocked", "description": f"BLOCKED: I won't rename that — {why}"}
            dest = p.parent / new_name
            dl, dwhy = classify_path_risk(dest)
            if dl == "blocked":
                return {"tier": "blocked", "description": f"BLOCKED: {dwhy}"}
            return {"tier": "confirm", "command": f"{p}  ->  {dest}",
                    "description": f"Rename '{p}' to '{new_name}'."}
        return {"tier": "confirm", "description": f"Rename '{path}' (couldn't resolve — confirming)."}
    except Exception:
        return {"tier": "confirm", "description": "Rename a file (couldn't classify — confirming)."}


def classify_copy_path(args: dict) -> dict:
    # Copying doesn't destroy the source, so only the DESTINATION is gated.
    src = str(args.get("source", ""))
    dst = str(args.get("dest", ""))
    try:
        b = _confirm_or_block_path(dst, "copy it there")
        if b:
            return b
        s, d = resolve_user_path(src), resolve_user_path(dst)
        return {"tier": "confirm", "command": f"{s or src}  ->  {d or dst}",
                "description": f"Copy '{s or src}' to '{d or dst}'."}
    except Exception:
        return {"tier": "confirm", "description": "Copy a file (couldn't classify — confirming)."}
