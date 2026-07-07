"""File system management — search, open, read/summarize, move/copy/rename,
create folders, and delete (delete ALWAYS reads back the exact path + confirms)."""
from __future__ import annotations

import shutil
from pathlib import Path

from core.confirmations import confirm_action
from core.skill_registry import register_skill
from skills.base import Skill, prop, tool

SEARCH_ROOTS = [Path.home(), Path.home() / "Desktop", Path.home() / "Documents",
                Path.home() / "Downloads"]


@register_skill
class FileManagerSkill(Skill):
    name = "file_manager"
    description = "Find, open, read, summarize, move, copy, rename, and delete files and folders (deletion always confirmed)."

    def tools(self) -> list[dict]:
        return [
            tool("search_files", "Search for files by name under the user's home/Desktop/Documents/Downloads.",
                 {"query": prop("string", "Filename or partial name"),
                  "limit": prop("integer", "Max results (default 15)")}, ["query"]),
            tool("read_file", "Read a text file's contents (truncated) so you can summarize it.",
                 {"path": prop("string", "Full path to the file")}, ["path"]),
            tool("open_file", "Open a file or folder in its default app / Explorer.",
                 {"path": prop("string", "Full path")}, ["path"]),
            tool("create_folder", "Create a new folder.",
                 {"path": prop("string", "Full path of the folder to create")}, ["path"]),
            tool("move_file", "Move or rename a file/folder.",
                 {"src": prop("string", "Source path"), "dst": prop("string", "Destination path")}, ["src", "dst"]),
            tool("copy_file", "Copy a file to a new location.",
                 {"src": prop("string", "Source path"), "dst": prop("string", "Destination path")}, ["src", "dst"]),
            tool("delete_file", "Delete a file or folder. Reads back the exact path and REQUIRES confirmation.",
                 {"path": prop("string", "Full path to delete")}, ["path"]),
        ]

    def execute(self, tool: str, args: dict) -> str:
        try:
            return getattr(self, f"_{tool}")(args)
        except Exception as exc:
            self.log(tool, args, "error")
            return f"File operation failed: {exc}"

    def _search_files(self, args) -> str:
        query = str(args.get("query", "")).lower()
        limit = int(args.get("limit", 15))
        hits: list[str] = []
        for root in SEARCH_ROOTS:
            if not root.exists():
                continue
            for p in root.rglob("*"):
                if query in p.name.lower():
                    hits.append(str(p))
                    if len(hits) >= limit:
                        break
            if len(hits) >= limit:
                break
        self.log("search_files", {"query": query, "found": len(hits)})
        return "Found:\n" + "\n".join(hits) if hits else f"No files matching '{query}'."

    def _read_file(self, args) -> str:
        path = Path(args["path"])
        if not path.is_file():
            return f"No file at {path}."
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return f"Couldn't read {path.name}: {exc}"
        self.log("read_file", {"path": str(path)})
        return f"Contents of {path.name} (first 4000 chars):\n{text[:4000]}"

    def _open_file(self, args) -> str:
        import os
        path = Path(args["path"])
        if not path.exists():
            return f"Nothing exists at {path}."
        os.startfile(str(path))
        self.log("open_file", {"path": str(path)})
        return f"Opened {path.name}."

    def _create_folder(self, args) -> str:
        path = Path(args["path"])
        path.mkdir(parents=True, exist_ok=True)
        self.log("create_folder", {"path": str(path)})
        return f"Created folder {path}."

    def _move_file(self, args) -> str:
        src, dst = Path(args["src"]), Path(args["dst"])
        if not src.exists():
            return f"Source {src} doesn't exist."
        shutil.move(str(src), str(dst))
        self.log("move_file", {"src": str(src), "dst": str(dst)})
        return f"Moved {src.name} to {dst}."

    def _copy_file(self, args) -> str:
        src, dst = Path(args["src"]), Path(args["dst"])
        if not src.exists():
            return f"Source {src} doesn't exist."
        shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)
        self.log("copy_file", {"src": str(src), "dst": str(dst)})
        return f"Copied {src.name} to {dst}."

    def _delete_file(self, args) -> str:
        path = Path(args["path"])
        if not path.exists():
            return f"Nothing exists at {path}."
        kind = "folder and everything in it" if path.is_dir() else "file"
        # Reads back the EXACT path before deleting (spec 7.2).
        if not confirm_action(f"Permanently delete this {kind}: {path}"):
            self.log("delete_file", {"path": str(path)}, "denied")
            return f"Kept {path.name} — deletion cancelled."
        shutil.rmtree(path) if path.is_dir() else path.unlink()
        self.log("delete_file", {"path": str(path)})
        return f"Deleted {path.name}, sir."
