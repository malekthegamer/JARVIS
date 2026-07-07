"""Code & dev assistant — run scripts/commands (with confirmation for anything
with side effects) and basic git helpers.

Code GENERATION is the brain's own job; this skill is the execution surface.
Any command that could change files / install packages / run generated code is
shown and confirmed first — never auto-run."""
from __future__ import annotations

import subprocess

from core.confirmations import confirm_action
from core.skill_registry import register_skill
from skills.base import Skill, prop, tool

SAFE_PREFIXES = ("python --version", "pip list", "git status", "git log", "git diff",
                 "dir", "echo", "where", "python -c \"print")


@register_skill
class CodeAssistantSkill(Skill):
    name = "code_assistant"
    description = "Run shell commands and scripts (confirmed when they have side effects) and basic git operations."

    def tools(self) -> list[dict]:
        return [
            tool("run_command", "Run a shell command and return its output. Side-effecting commands require confirmation.",
                 {"command": prop("string", "The command line to run"),
                  "cwd": prop("string", "Optional working directory")}, ["command"]),
            tool("git", "Run a basic git operation: status, add, commit, push. Force-push/rewrite is refused.",
                 {"operation": prop("string", "status | add | commit | push"),
                  "message": prop("string", "Commit message (for commit)"),
                  "cwd": prop("string", "Repo directory")}, ["operation"]),
        ]

    def execute(self, tool: str, args: dict) -> str:
        try:
            return getattr(self, f"_{tool}")(args)
        except Exception as exc:
            self.log(tool, args, "error")
            return f"Command failed: {exc}"

    def _is_safe(self, command: str) -> bool:
        return command.strip().lower().startswith(SAFE_PREFIXES)

    def _run_command(self, args) -> str:
        command = str(args.get("command", "")).strip()
        cwd = args.get("cwd") or None
        if not command:
            return "No command given."
        if not self._is_safe(command):
            if not confirm_action(f"Run this command (it may change files or install software):\n    {command}"):
                self.log("run_command", {"command": command}, "denied")
                return "Command cancelled — nothing was run."
        result = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True, timeout=120)
        self.log("run_command", {"command": command})
        out = (result.stdout or "") + (result.stderr or "")
        return f"Exit {result.returncode}:\n{out[:3000]}" if out.strip() else f"Done (exit {result.returncode}, no output)."

    def _git(self, args) -> str:
        op = str(args.get("operation", "")).lower()
        cwd = args.get("cwd") or None
        cmds = {
            "status": ["git", "status", "--short"],
            "add": ["git", "add", "-A"],
            "commit": ["git", "commit", "-m", str(args.get("message", "update"))],
            "push": ["git", "push"],
        }
        if op not in cmds:
            return "Supported git ops: status, add, commit, push."
        if op in ("commit", "push"):
            detail = f" -m \"{args.get('message', '')}\"" if op == "commit" else ""
            if not confirm_action(f"git {op}{detail}"):
                self.log("git", {"operation": op}, "denied")
                return f"git {op} cancelled."
        result = subprocess.run(cmds[op], cwd=cwd, capture_output=True, text=True)
        self.log("git", {"operation": op})
        return (result.stdout or result.stderr or f"git {op} done.")[:2000]
