"""PC-control primitives — the verbs (spec Part 1) + the executor that wraps
every call in act → observe → verify and broadcasts EXECUTING to the HUD.

The registry maps tool name -> {fn, schema, tier}. All slice-2 primitives
are AUTO tier; the tier field exists now so slice 3's CONFIRM gate is a
lookup, not a refactor. execute() never raises — failures become result
strings the model reports honestly.
"""
from __future__ import annotations

import os
import time

from jarvis.core.confirmations import Decision, confirmations
from jarvis.core.settings_store import settings
from jarvis.primitives import apps, files, screen, ui_tree, windows
from jarvis.state import AgentState, broadcaster

# Verify: how long we poll for the launched app's window to appear.
WINDOW_WAIT_S = 5.0
# Verify: minimum changed-screen fraction that counts as visible change.
DIFF_MEANINGFUL = 0.01


def _run_launch_app(args: dict) -> str:
    """Act (launch) + observe (ui tree, screenshots) + verify (both signals),
    reported separately so the model — and the user — see the evidence."""
    name = str(args.get("name", "")).strip()
    before = screen.capture_screen()
    result = apps.launch_app(name)
    if not result["ok"]:
        return f"FAILED: {result['message']}"

    if result["resolved"] and not result["pid"]:  # URI launch (ms-settings:)
        needle = None
    else:
        needle = os.path.splitext(os.path.basename(result["resolved"]))[0]

    window_ok = False
    if needle:
        deadline = time.time() + WINDOW_WAIT_S
        while time.time() < deadline:
            if ui_tree.window_present(needle):
                window_ok = True
                break
            time.sleep(0.4)
    after = screen.capture_screen()
    frac = screen.screenshot_diff(before, after)

    verify = []
    if needle:
        verify.append(f"window matching '{needle}' present={window_ok}")
    verify.append(f"screen changed {frac:.1%}"
                  + ("" if frac >= DIFF_MEANINGFUL else " (below meaningful threshold)"))
    verdict = "VERIFIED" if (window_ok or (needle is None and frac >= DIFF_MEANINGFUL)) \
        else "NOT CONFIRMED — tell the user honestly"
    return f"{result['message']} VERIFY [{verdict}]: {'; '.join(verify)}."


def _run_read_ui_tree(args: dict) -> str:
    return ui_tree.read_ui_tree()


def _run_delete_file(args: dict) -> str:
    r = files.delete_file(str(args.get("name", "")))
    return ("OK: " if r["ok"] else "FAILED: ") + r["message"]


def _run_close_window(args: dict) -> str:
    r = windows.close_window(str(args.get("title", "")))
    return ("OK: " if r["ok"] else "FAILED: ") + r["message"]


PRIMITIVES: dict[str, dict] = {
    "launch_app": {
        "fn": _run_launch_app,
        "tier": "auto",
        "schema": {
            "name": "launch_app",
            "description": ("Launch an installed application on the user's PC by name "
                            "(e.g. notepad, calculator, chrome, spotify). Use ONLY when "
                            "the user asks to open, launch, or start an app — never for "
                            "questions or conversation."),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string",
                                        "description": "Application name to launch"}},
                "required": ["name"],
            },
        },
    },
    "read_ui_tree": {
        "fn": _run_read_ui_tree,
        "tier": "auto",
        "schema": {
            "name": "read_ui_tree",
            "description": ("List the windows and controls currently visible on the "
                            "user's screen. Use ONLY when the user asks what is open or "
                            "on screen, or to double-check the result of an action."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "delete_file": {
        "fn": _run_delete_file,
        "tier": "confirm",
        "describe": files.describe_delete,
        "schema": {
            "name": "delete_file",
            "description": ("Delete a file from the agent workspace "
                            "(data/agent_files). The user must approve via a "
                            "confirmation prompt before anything is deleted. Use "
                            "ONLY when the user explicitly asks to delete a file."),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string",
                                        "description": "File name inside the agent workspace"}},
                "required": ["name"],
            },
        },
    },
    "close_window": {
        "fn": _run_close_window,
        "tier": "confirm",
        "describe": windows.describe_close,
        "schema": {
            "name": "close_window",
            "description": ("Close an open window by its title (or part of it). "
                            "The user must approve via a confirmation prompt before "
                            "it closes. Use ONLY when the user explicitly asks to "
                            "close a window or app."),
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string",
                                         "description": "Window title, or a distinctive part of it"}},
                "required": ["title"],
            },
        },
    },
}

_CANCEL_REASONS = {
    "declined": "the user declined",
    "timeout": "no response from the user within {t:.0f} seconds",
    "superseded": "another confirmation was already pending",
    "error": "the confirmation prompt could not be shown",
}


def tools_schema() -> list[dict]:
    return [p["schema"] for p in PRIMITIVES.values()]


def execute(name: str, args: dict) -> str:
    """Run one primitive. CONFIRM-tier primitives pass the fail-closed gate
    first; only an explicit user approval reaches EXECUTING. Never raises."""
    prim = PRIMITIVES.get(name)
    if prim is None:
        return f"Unknown tool: {name}"
    args = args or {}
    try:
        if prim["tier"] == "confirm":
            cancelled = _confirm_gate(prim, name, args)
            if cancelled is not None:
                return cancelled
        broadcaster.set(AgentState.EXECUTING, detail=name)
        try:
            return prim["fn"](args)
        except Exception as exc:
            return f"Tool {name} crashed: {exc}"
    finally:
        # Control returns to the model round; think()'s finally still lands IDLE.
        broadcaster.set(AgentState.THINKING)


def _confirm_gate(prim: dict, name: str, args: dict) -> str | None:
    """None = approved, proceed. A string = cancelled, return it as the tool
    result. Any internal failure reads as cancelled (fail closed)."""
    try:
        description = prim["describe"](args)
        if description is None:
            return "FAILED: nothing matching that to act on right now."
        broadcaster.set(AgentState.CONFIRMING, detail=name)
        timeout_s = float(settings.get("confirm.timeout_s", 30))
        decision = confirmations.request(description, timeout_s=timeout_s)
    except Exception:
        decision, description, timeout_s = Decision(False, "error"), name, 0.0
    if decision.approved:
        return None
    reason = _CANCEL_REASONS.get(decision.reason, decision.reason).format(t=timeout_s)
    return (f"CANCELLED ({reason}): {description}. "
            f"Do not retry — acknowledge this to the user.")
