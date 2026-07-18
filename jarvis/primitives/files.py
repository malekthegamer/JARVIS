"""delete_file — CONFIRM tier, caged to the agent workspace.

The workspace is data/agent_files/ and NOTHING outside it is reachable:
absolute paths are rejected outright, and the resolved target (symlinks
followed) must still live under the resolved workspace before any
filesystem call happens. Arbitrary-path file ops are a later slice.

Slice 26: deletion QUARANTINES instead of unlinking — the file moves to
data/agent_trash/<token>/<original relative path>, so "undo" is a plain
move back. The trash root lives OUTSIDE the workspace deliberately: the
cage invariant ("everything under agent_files is reachable by the file
tools") stays clean — search_files can't list quarantined files and
delete_file can't target them. Retention is capped (newest 20); past the
cap the oldest quarantine is purged permanently, so the undo window for
deletions is bounded, not infinite — a documented trade-off, not a bug.
"""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from jarvis import config

AGENT_FILES_DIR = config.DATA_DIR / "agent_files"
AGENT_FILES_DIR.mkdir(parents=True, exist_ok=True)

# Quarantined deletions kept before the oldest is purged for real.
TRASH_MAX_ENTRIES = 20


def _trash_root() -> Path:
    """Sibling of the workspace, DERIVED from it — tests that re-point
    AGENT_FILES_DIR at tmp_path automatically isolate the trash too."""
    return AGENT_FILES_DIR.parent / "agent_trash"


def _purge_old_trash() -> None:
    """Keep the newest TRASH_MAX_ENTRIES quarantine dirs. Tokens are
    time_ns-prefixed, so lexicographic order IS age order. Never raises."""
    try:
        dirs = sorted(p for p in _trash_root().iterdir() if p.is_dir())
        for stale in dirs[:-TRASH_MAX_ENTRIES] if len(dirs) > TRASH_MAX_ENTRIES else []:
            shutil.rmtree(stale, ignore_errors=True)
    except Exception:
        pass

_README = AGENT_FILES_DIR / "README.md"
if not _README.exists():
    _README.write_text(
        "# JARVIS agent workspace\n\n"
        "Files JARVIS may manage (create/delete on request) live here.\n"
        "Nothing outside this folder is reachable by the agent's file tools.\n",
        encoding="utf-8",
    )


def _contained(name: str) -> Path | None:
    """Resolve `name` inside the workspace, or None if it escapes.

    Two belts: (1) lexical — reject absolute paths and any component that is
    empty, all dots ('..', '....'), or ends in a dot/space (Windows strips
    those on FS ops, so such names alias other files platform-dependently);
    (2) resolved containment — the final path, symlinks followed, must still
    live under the workspace."""
    if not name or Path(name).is_absolute():
        return None
    for part in Path(name).parts:
        stripped = part.replace(".", "")
        if not stripped or part != part.rstrip(". "):
            return None
    try:
        target = (AGENT_FILES_DIR / name).resolve()
        base = AGENT_FILES_DIR.resolve()
        if target == base or not target.is_relative_to(base):
            return None
        return target
    except (OSError, ValueError):
        return None


def delete_file(name: str) -> dict:
    """CONFIRM tier. Returns {"ok", "message"[, "undo_token"]} — never raises.

    Slice 26: the file is MOVED to quarantine, not unlinked; "undo_token"
    names its quarantine dir so restore_file() can bring it back. From the
    user's point of view the file is gone from the workspace either way."""
    name = str(name or "").strip()
    try:
        target = _contained(name)
        if target is None:
            return {"ok": False,
                    "message": f"Refused: '{name}' is outside my workspace "
                               f"({AGENT_FILES_DIR}). I only delete files in there."}
        if not target.exists():
            return {"ok": False,
                    "message": f"No file named '{name}' in my workspace."}
        if target.is_dir():
            return {"ok": False,
                    "message": f"'{name}' is a folder — I only delete files."}
        rel = target.relative_to(AGENT_FILES_DIR.resolve())
        token = f"{time.time_ns():019d}-{uuid.uuid4().hex[:8]}"
        dest = _trash_root() / token / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(dest))
        _purge_old_trash()
        return {"ok": True, "undo_token": token,
                "message": f"Deleted '{name}' from my workspace."}
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't delete '{name}': {exc}"}


def restore_file(token: str) -> dict:
    """Undo a quarantined deletion: move the file back to its original
    workspace path. Fails closed — never overwrites a newer occupant, and
    reports honestly when the quarantine entry was already purged (the
    bounded-retention trade-off). Never raises."""
    try:
        qdir = _trash_root() / str(token or "")
        if not qdir.is_dir():
            return {"ok": False,
                    "message": "that deleted file is no longer in quarantine "
                               "(older deletions are purged) — I can't restore it."}
        srcs = [p for p in qdir.rglob("*") if p.is_file()]
        if not srcs:
            return {"ok": False,
                    "message": "the quarantine entry is empty — nothing to restore."}
        src = srcs[0]
        rel = src.relative_to(qdir)
        dest = AGENT_FILES_DIR / rel
        if dest.exists():
            return {"ok": False,
                    "message": f"a file already exists at '{rel.as_posix()}' — "
                               f"I won't overwrite it to restore the old one."}
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        shutil.rmtree(qdir, ignore_errors=True)
        return {"ok": True,
                "message": f"restored '{rel.as_posix()}' to my workspace."}
    except Exception as exc:
        return {"ok": False, "message": f"couldn't restore that file: {exc}"}


def search_files(query: str = "", ext: str = "", within_days: float = 0) -> dict:
    """AUTO tier (slice 8). Read-only search of the agent workspace by name
    substring, optional extension, optional modified-within-days. The query
    is a FILTER over names inside the cage, never a path — and every hit is
    re-checked with the same two-belt containment as delete_file, so a
    symlinked escape can never be listed. Never raises."""
    query = str(query or "").strip().lower()
    ext = str(ext or "").strip().lstrip(".").lower()
    try:
        within_days = float(within_days or 0)
    except (TypeError, ValueError):
        within_days = 0
    if not query and not ext and within_days <= 0:
        return {"ok": False, "matches": [],
                "message": "Give me a file name, an extension, or an age "
                           "(days) to search for."}
    matches: list[dict] = []
    try:
        import time
        cutoff = time.time() - within_days * 86400 if within_days > 0 else None
        base = AGENT_FILES_DIR.resolve()
        for p in sorted(AGENT_FILES_DIR.rglob("*")):
            if len(matches) >= 50:
                break
            try:
                if not p.is_file():
                    continue
                rel = p.relative_to(AGENT_FILES_DIR).as_posix()
                if _contained(rel) is None:  # same cage as delete_file
                    continue
                if query and query not in rel.lower():
                    continue
                if ext and p.suffix.lstrip(".").lower() != ext:
                    continue
                stat = p.stat()
                if cutoff is not None and stat.st_mtime < cutoff:
                    continue
                matches.append({
                    "name": rel, "size": stat.st_size,
                    "modified": time.strftime("%Y-%m-%d %H:%M",
                                              time.localtime(stat.st_mtime))})
            except OSError:
                continue
    except Exception as exc:
        return {"ok": False, "matches": [],
                "message": f"Search failed: {exc}"}
    crit = " ".join(filter(None, [
        f"name contains '{query}'" if query else "",
        f"type .{ext}" if ext else "",
        f"modified within {within_days:g} day(s)" if within_days > 0 else ""]))
    if not matches:
        return {"ok": True, "matches": [],
                "message": f"No files matching {crit} in my workspace."}
    listing = "; ".join(f"{m['name']} ({m['size']}B, {m['modified']})"
                        for m in matches)
    return {"ok": True, "matches": matches,
            "message": f"Found {len(matches)} file(s) matching {crit}: {listing}."}


def describe_delete(args: dict) -> str | None:
    """Human text for the CONFIRM modal. None would skip the modal — deletion
    always prompts, even for files that don't exist (the check runs after
    approval, so the answer the user gives is about intent, not existence)."""
    name = str(args.get("name", "")).strip()
    return f"Delete file '{name}' from the agent workspace ({AGENT_FILES_DIR})"
