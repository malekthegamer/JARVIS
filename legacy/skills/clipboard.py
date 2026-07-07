"""Clipboard — read/write the Windows clipboard ('summarize what I just copied')."""
from __future__ import annotations

from core.skill_registry import register_skill
from skills.base import Skill, prop, tool


@register_skill
class ClipboardSkill(Skill):
    name = "clipboard"
    description = "Read and write the system clipboard, e.g. to summarize copied text."

    def tools(self) -> list[dict]:
        return [
            tool("read_clipboard", "Get the current text contents of the clipboard."),
            tool("write_clipboard", "Put text onto the clipboard.",
                 {"text": prop("string", "Text to copy")}, ["text"]),
        ]

    def execute(self, tool: str, args: dict) -> str:
        try:
            import pyperclip  # lazy
            if tool == "read_clipboard":
                text = pyperclip.paste()
                self.log("read_clipboard")
                return f"Clipboard contains:\n{text[:4000]}" if text else "The clipboard is empty."
            if tool == "write_clipboard":
                pyperclip.copy(str(args.get("text", "")))
                self.log("write_clipboard")
                return "Copied to clipboard."
        except Exception as exc:
            return f"Clipboard access failed: {exc}"
        return f"Unknown clipboard tool {tool}."
