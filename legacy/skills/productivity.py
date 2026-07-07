"""Productivity — local reminders/to-dos and quick notes.

Reminders live in data/reminders.json and are checked by the scheduler; a due
reminder surfaces as a DASHBOARD NOTIFICATION only (reactive-only rule).
Google Calendar is an optional seam that degrades to local-only silently.
"""
from __future__ import annotations

import json
from datetime import datetime

import config
from core import notifications
from core.skill_registry import register_skill
from skills.base import Skill, prop, tool


def _load() -> list:
    if config.REMINDERS_FILE.exists():
        try:
            return json.loads(config.REMINDERS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save(items: list) -> None:
    config.REMINDERS_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


@register_skill
class ProductivitySkill(Skill):
    name = "productivity"
    description = "Manage local reminders and to-dos, and capture quick notes."

    def tools(self) -> list[dict]:
        return [
            tool("add_reminder", "Add a reminder or to-do item.",
                 {"text": prop("string", "What to be reminded of"),
                  "when": prop("string", "Optional due time, e.g. '2026-07-08 15:00' or free text")}, ["text"]),
            tool("list_reminders", "List all current reminders and to-dos."),
            tool("complete_reminder", "Mark a reminder done by its number.",
                 {"number": prop("integer", "The reminder's list number")}, ["number"]),
            tool("add_note", "Append a quick note to the local notes file.",
                 {"text": prop("string", "Note content")}, ["text"]),
        ]

    def execute(self, tool: str, args: dict) -> str:
        try:
            return getattr(self, f"_{tool}")(args)
        except Exception as exc:
            self.log(tool, args, "error")
            return f"Productivity action failed: {exc}"

    def _add_reminder(self, args) -> str:
        items = _load()
        items.append({"text": str(args.get("text", "")), "when": str(args.get("when", "")),
                      "created": datetime.now().isoformat(timespec="minutes"), "done": False})
        _save(items)
        self.log("add_reminder", {"text": args.get("text", "")})
        return f"Reminder added: {args.get('text')}" + (f" (due {args.get('when')})" if args.get("when") else "")

    def _list_reminders(self, args) -> str:
        items = [i for i in _load() if not i["done"]]
        self.log("list_reminders")
        if not items:
            return "No active reminders."
        return "Reminders:\n" + "\n".join(
            f"{n}. {i['text']}" + (f" — due {i['when']}" if i["when"] else "")
            for n, i in enumerate(items, 1))

    def _complete_reminder(self, args) -> str:
        num = int(args.get("number", 0))
        items = _load()
        active = [i for i in items if not i["done"]]
        if 1 <= num <= len(active):
            active[num - 1]["done"] = True
            _save(items)
            self.log("complete_reminder", {"number": num})
            return f"Marked done: {active[num - 1]['text']}"
        return "No reminder with that number."

    def _add_note(self, args) -> str:
        text = str(args.get("text", ""))
        stamp = datetime.now().isoformat(timespec="minutes")
        with open(config.NOTES_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n- [{stamp}] {text}")
        self.log("add_note")
        return "Noted."

    # -- scheduler hook: surface due reminders as dashboard notifications --
    def check_due(self) -> None:
        now = datetime.now()
        for item in _load():
            if item["done"] or not item.get("when"):
                continue
            try:
                due = datetime.fromisoformat(item["when"])
            except ValueError:
                continue
            if due <= now and not item.get("notified"):
                notifications.notify("Reminder due", item["text"], "productivity")
                item["notified"] = True
        # persist the notified flag
        items = _load()
        for i in items:
            if not i["done"] and i.get("when"):
                try:
                    if datetime.fromisoformat(i["when"]) <= now:
                        i["notified"] = True
                except ValueError:
                    pass
        _save(items)
