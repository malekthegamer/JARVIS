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

# Slice 35: this text must stay TRUE. The previous version claimed "Nothing
# outside this folder is reachable by the agent's file tools" — accurate until
# slices 32-33 gave fsaccess read/write/move/rename/copy/delete anywhere on the
# PC, after which it was a false user-facing SAFETY claim. Overstating the cage
# is worse than describing it plainly.
_README_TEXT = (
    "# JARVIS agent workspace\n"
    "\n"
    "This folder is the cage for JARVIS's **workspace** file tools —\n"
    "`write_file`, `read_file`, `delete_file`, `search_files`. Those four\n"
    "cannot touch anything outside this folder. Deleting one quarantines it\n"
    "first, so it can be restored.\n"
    "\n"
    "It is NOT a limit on JARVIS as a whole. The real-filesystem tools\n"
    "(`list_directory`, `read_path`, `write_path`, `move_path`, `rename_path`,\n"
    "`copy_path`, `delete_path`, `create_shortcut`) reach anywhere on this PC.\n"
    "Every one of their changes asks you first and shows the exact resolved\n"
    "path; deletes and overwrites go to the Recycle Bin; catastrophic targets\n"
    "are refused outright. Turn them off entirely with `fs.enabled` in\n"
    "settings.\n"
)


def _ensure_readme() -> None:
    """Write JARVIS's own workspace README, refreshing it when the text has
    CHANGED — not only when the file is missing.

    The original `if not _README.exists()` guard meant an existing install
    could never be corrected: the pre-slice-32 containment claim survived on
    disk long after fsaccess made it false. Content-driven refresh is what
    makes a fixed falsehood actually reach the user. Never raises — a
    read-only workspace must not break import."""
    try:
        if _README.exists() and _README.read_text(encoding="utf-8") == _README_TEXT:
            return
        _README.write_text(_README_TEXT, encoding="utf-8")
    except Exception:
        pass


_ensure_readme()


def _quarantine(target: Path) -> str:
    """Move a caged file into the trash under a fresh token, purge old entries,
    and return the token (so restore_file can bring it back). Shared by
    delete_file (slice 26) and write_file's overwrite path (slice 30) — the one
    place a workspace file is set aside rather than destroyed."""
    rel = target.relative_to(AGENT_FILES_DIR.resolve())
    token = f"{time.time_ns():019d}-{uuid.uuid4().hex[:8]}"
    dest = _trash_root() / token / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(target), str(dest))
    _purge_old_trash()
    return token


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
        token = _quarantine(target)
        return {"ok": True, "undo_token": token,
                "message": f"Deleted '{name}' from my workspace."}
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't delete '{name}': {exc}"}


def restore_file(token: str, over: bool = False) -> dict:
    """Move a quarantined file back to its original workspace path. Reports
    honestly when the entry was already purged (the bounded-retention
    trade-off). Never raises.

    `over=False` (default, the delete-undo path): REFUSES if something now sits
    at the destination — undoing a delete must never clobber a newer file.
    `over=True` (the slice-30 overwrite-undo path): the destination IS expected
    to hold the post-overwrite content, so replace it with the quarantined
    original (that's exactly what "undo the overwrite" means)."""
    try:
        qdir = _trash_root() / str(token or "")
        if not qdir.is_dir():
            return {"ok": False,
                    "message": "that file is no longer in quarantine "
                               "(older entries are purged) — I can't restore it."}
        srcs = [p for p in qdir.rglob("*") if p.is_file()]
        if not srcs:
            return {"ok": False,
                    "message": "the quarantine entry is empty — nothing to restore."}
        src = srcs[0]
        rel = src.relative_to(qdir)
        dest = AGENT_FILES_DIR / rel
        if dest.exists():
            if not over:
                return {"ok": False,
                        "message": f"a file already exists at '{rel.as_posix()}' — "
                                   f"I won't overwrite it to restore the old one."}
            dest.unlink()                     # overwrite-undo: replace the newer content
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        shutil.rmtree(qdir, ignore_errors=True)
        return {"ok": True,
                "message": f"restored '{rel.as_posix()}' to my workspace."}
    except Exception as exc:
        return {"ok": False, "message": f"couldn't restore that file: {exc}"}


# Content-search bounds. A grep over the workspace runs on the recall path, so
# it must stay cheap and must never try to read a binary or a huge file.
_CONTENT_MAX_BYTES = 256 * 1024
_CONTENT_TEXT_EXTS = {"md", "txt", "json", "csv", "log", "yaml", "yml", "ini",
                      "py", "js", "html", "css", "xml", "toml", ""}


def _content_hit(path, needle: str) -> str | None:
    """The first matching line, or None. Never raises — a file that cannot be
    read is simply not a hit."""
    try:
        if path.suffix.lstrip(".").lower() not in _CONTENT_TEXT_EXTS:
            return None
        if path.stat().st_size > _CONTENT_MAX_BYTES:
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
        if "\x00" in text[:2048]:          # binary that slipped past the suffix
            return None
        for line in text.splitlines():
            if needle in line.lower():
                return line.strip()[:200]
        return None
    except Exception:
        return None


def search_files(query: str = "", ext: str = "", within_days: float = 0,
                 contains: str = "") -> dict:
    """AUTO tier (slice 8). Read-only search of the agent workspace by name
    substring, optional extension, optional modified-within-days. The query
    is a FILTER over names inside the cage, never a path — and every hit is
    re-checked with the same two-belt containment as delete_file, so a
    symlinked escape can never be listed. Never raises.

    SLICE 61 added `contains`: match what is WRITTEN in a note, not just its
    filename. Without it the workspace was storage rather than memory — a note
    could be saved and then never found again unless you remembered the
    filename.

    This is deliberately a LITERAL search, not an embedding one. Slice 34
    measured the embedder's ceiling (0.818 recall, ~18% of paraphrases missing)
    and proved that residual unfixable with the shipped model; grep has no such
    ceiling, which is precisely the point of keeping knowledge as readable text.
    """
    query = str(query or "").strip().lower()
    ext = str(ext or "").strip().lstrip(".").lower()
    try:
        contains = str(contains or "").strip().lower()
    except Exception:
        contains = ""
    try:
        within_days = float(within_days or 0)
    except (TypeError, ValueError):
        within_days = 0
    if not query and not ext and within_days <= 0 and not contains:
        return {"ok": False, "matches": [],
                "message": "Give me a file name, an extension, an age (days), "
                           "or some text to look for inside the files."}
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
                excerpt = None
                if contains:
                    excerpt = _content_hit(p, contains)
                    if excerpt is None:
                        continue      # the text isn't in this file
                hit = {"name": rel, "size": stat.st_size,
                       "modified": time.strftime("%Y-%m-%d %H:%M",
                                                 time.localtime(stat.st_mtime))}
                if excerpt is not None:
                    # Return the LINE, so one call answers the question instead
                    # of costing a second read_file round.
                    hit["excerpt"] = excerpt
                matches.append(hit)
            except OSError:
                continue
    except Exception as exc:
        return {"ok": False, "matches": [],
                "message": f"Search failed: {exc}"}
    crit = " ".join(filter(None, [
        f"name contains '{query}'" if query else "",
        f"text contains '{contains}'" if contains else "",
        f"type .{ext}" if ext else "",
        f"modified within {within_days:g} day(s)" if within_days > 0 else ""]))
    if not matches:
        return {"ok": True, "matches": [],
                "message": f"No files matching {crit} in my workspace."}
    # Carry the matching LINE into the message, not just the filename — the
    # point of content search is that one call answers the question.
    listing = "; ".join(
        (f"{m['name']}: \"{m['excerpt']}\"" if m.get("excerpt")
         else f"{m['name']} ({m['size']}B, {m['modified']})")
        for m in matches)
    return {"ok": True, "matches": matches,
            "message": f"Found {len(matches)} file(s) matching {crit}: {listing}."}


def describe_delete(args: dict) -> str | None:
    """Human text for the CONFIRM modal. None would skip the modal — deletion
    always prompts, even for files that don't exist (the check runs after
    approval, so the answer the user gives is about intent, not existence)."""
    name = str(args.get("name", "")).strip()
    return f"Delete file '{name}' from the agent workspace ({AGENT_FILES_DIR})"


# ---------------------------------------------------------------- slice 30:
# caged file authoring — write_file (create/overwrite) + read_file. Both go
# through the SAME _contained() cage as delete_file; an overwrite quarantines
# the prior content so it's undoable (slice-26 machinery).

def _max_kb(key: str, default: int) -> int:
    from jarvis.core.settings_store import settings
    try:
        return max(1, int(settings.get(key, default)))
    except (TypeError, ValueError):
        return default


def write_file(name: str, content: str) -> dict:
    """Create or overwrite a UTF-8 text file in the workspace. Caged by
    _contained(). An OVERWRITE quarantines the prior content first (so undo can
    restore it); a CREATE makes parent subdirs as needed. Content is capped at
    files.max_write_kb. Returns {ok, message, undo_kind, undo_token?} — never
    raises."""
    name = str(name or "").strip()
    content = "" if content is None else str(content)
    try:
        target = _contained(name)
        if target is None:
            return {"ok": False,
                    "message": f"Refused: '{name}' is outside my workspace "
                               f"({AGENT_FILES_DIR}). I only write files in there."}
        if target.exists() and target.is_dir():
            return {"ok": False,
                    "message": f"'{name}' is a folder — I can't write over it."}
        cap = _max_kb("files.max_write_kb", 256)
        if len(content.encode("utf-8")) > cap * 1024:
            return {"ok": False,
                    "message": f"That content is too large to write "
                               f"(> {cap} KB). Refused before writing anything."}
        overwrite = target.exists()
        undo_token = _quarantine(target) if overwrite else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        verb = "Overwrote" if overwrite else "Wrote"
        return {"ok": True,
                "undo_kind": "overwrite" if overwrite else "create",
                "undo_token": undo_token,
                "message": f"{verb} '{name}' ({len(content)} chars) in my workspace."}
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't write '{name}': {exc}"}


def read_file(name: str) -> dict:
    """Read a UTF-8 text file from the workspace (AUTO). Caged by _contained().
    Capped at files.max_read_kb with an honest truncation note. Returns
    {ok, message, content} — never raises."""
    name = str(name or "").strip()
    try:
        target = _contained(name)
        if target is None:
            return {"ok": False, "content": "",
                    "message": f"Refused: '{name}' is outside my workspace "
                               f"({AGENT_FILES_DIR}). I only read files in there."}
        if not target.exists():
            return {"ok": False, "content": "",
                    "message": f"No file named '{name}' in my workspace."}
        if target.is_dir():
            return {"ok": False, "content": "",
                    "message": f"'{name}' is a folder — I only read files."}
        cap = _max_kb("files.max_read_kb", 256) * 1024
        raw = target.read_text(encoding="utf-8", errors="replace")
        if len(raw) > cap:
            return {"ok": True, "content": raw[:cap],
                    "message": f"Read '{name}' (truncated to {cap} chars of "
                               f"{len(raw)})."}
        return {"ok": True, "content": raw,
                "message": f"Read '{name}' ({len(raw)} chars)."}
    except Exception as exc:
        return {"ok": False, "content": "",
                "message": f"Couldn't read '{name}': {exc}"}


def classify_write_file(args: dict) -> dict:
    """Dynamic tier (slice 30): AUTO to CREATE a new file, CONFIRM to OVERWRITE
    an existing one (the modal names it). Fail-closed to confirm. Never raises."""
    try:
        name = str(args.get("name", "")).strip()
        target = _contained(name)
        if target is not None and target.exists() and target.is_file():
            return {"tier": "confirm",
                    "description": f"Overwrite file '{name}' in the agent "
                                   f"workspace ({AGENT_FILES_DIR}) — its current "
                                   f"contents will be replaced."}
        return {"tier": "auto", "description": f"Write file '{name}'"}
    except Exception:
        return {"tier": "confirm",
                "description": "Write a file (couldn't classify — confirming)."}
