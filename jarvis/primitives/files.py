"""delete_file — CONFIRM tier, caged to the agent workspace.

The workspace is data/agent_files/ and NOTHING outside it is reachable:
absolute paths are rejected outright, and the resolved target (symlinks
followed) must still live under the resolved workspace before any
filesystem call happens. Arbitrary-path file ops are a later slice.
"""
from __future__ import annotations

from pathlib import Path

from jarvis import config

AGENT_FILES_DIR = config.DATA_DIR / "agent_files"
AGENT_FILES_DIR.mkdir(parents=True, exist_ok=True)

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
    """CONFIRM tier. Returns {"ok", "message"} — never raises."""
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
        target.unlink()
        return {"ok": True, "message": f"Deleted '{name}' from my workspace."}
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't delete '{name}': {exc}"}


def describe_delete(args: dict) -> str | None:
    """Human text for the CONFIRM modal. None would skip the modal — deletion
    always prompts, even for files that don't exist (the check runs after
    approval, so the answer the user gives is about intent, not existence)."""
    name = str(args.get("name", "")).strip()
    return f"Delete file '{name}' from the agent workspace ({AGENT_FILES_DIR})"
