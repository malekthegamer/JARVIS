"""Centralized confirmation gate for destructive / irreversible actions.

EVERY skill that deletes, sends, submits, purchases, executes generated code,
or otherwise does something hard to undo calls confirm_action() first.

The frontend is pluggable: terminal y/n by default; the dashboard replaces it
with a modal (server.py sets its own frontend that round-trips a WebSocket
confirm prompt). Both the request and the user's answer hit the audit log.
"""
from __future__ import annotations

from typing import Callable

from core import audit_log

_frontend: Callable[[str], bool] | None = None


def set_frontend(fn: Callable[[str], bool]) -> None:
    """Install the active confirmation UI (terminal prompt or dashboard modal)."""
    global _frontend
    _frontend = fn


def _terminal_confirm(description: str) -> bool:
    try:
        answer = input(f"\n⚠  JARVIS wants to: {description}\n   Allow? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def confirm_action(description: str) -> bool:
    """Ask the user to approve an action. Returns False unless explicitly approved."""
    audit_log.log_action("confirmation", "requested", {"description": description}, "pending")
    frontend = _frontend or _terminal_confirm
    try:
        approved = bool(frontend(description))
    except Exception:
        approved = False  # a broken frontend must fail CLOSED, never approve
    audit_log.log_action(
        "confirmation", "answered", {"description": description},
        "approved" if approved else "denied",
    )
    return approved
