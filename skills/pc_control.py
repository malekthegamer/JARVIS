"""PC & OS control — launch/close apps, windows, input, system settings.

Destructive/hard-to-undo actions (close app, lock/sleep/restart) gate on
confirm_action first. All heavy libs are lazy-imported so importing this skill
never drags in pyautogui at startup.
"""
from __future__ import annotations

import os
import subprocess

from core.confirmations import confirm_action
from core.skill_registry import register_skill
from skills.base import Skill, prop, tool

# Friendly name -> launch target (Windows). Extend freely.
APP_ALIASES = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe",
    "paint": "mspaint.exe", "explorer": "explorer.exe", "file explorer": "explorer.exe",
    "cmd": "cmd.exe", "terminal": "wt.exe", "powershell": "powershell.exe",
    "task manager": "taskmgr.exe", "settings": "ms-settings:",
    "chrome": "chrome", "edge": "msedge", "firefox": "firefox",
    "spotify": "spotify", "discord": "discord", "steam": "steam",
    "vscode": "code", "vs code": "code", "code": "code",
}


@register_skill
class PCControlSkill(Skill):
    name = "pc_control"
    description = "Open and close applications, control volume, lock/sleep/restart the PC, and send keyboard hotkeys."

    def tools(self) -> list[dict]:
        return [
            tool("open_app", "Launch an application by name (e.g. notepad, chrome, spotify).",
                 {"app": prop("string", "Application name or alias")}, ["app"]),
            tool("close_app", "Close an application by its process/window name. Requires confirmation.",
                 {"app": prop("string", "Application/process name to close")}, ["app"]),
            tool("list_windows", "List the titles of currently open windows."),
            tool("set_volume", "Set the master volume to a percentage (0-100).",
                 {"percent": prop("integer", "Volume level 0-100")}, ["percent"]),
            tool("media_key", "Press a media key: playpause, next, previous, mute, volumeup, volumedown.",
                 {"key": prop("string", "One of: playpause, next, previous, mute, volumeup, volumedown")}, ["key"]),
            tool("hotkey", "Press a keyboard hotkey combo, keys separated by '+' (e.g. 'ctrl+shift+esc').",
                 {"combo": prop("string", "Keys joined by '+'")}, ["combo"]),
            tool("power_action", "Lock, sleep, or restart the PC. Requires confirmation.",
                 {"action": prop("string", "One of: lock, sleep, restart")}, ["action"]),
        ]

    def execute(self, tool: str, args: dict) -> str:
        try:
            return getattr(self, f"_{tool}")(args)
        except Exception as exc:  # never crash the brain loop
            self.log(tool, args, "error")
            return f"Couldn't complete {tool}: {exc}"

    # ---- tools ----
    def _open_app(self, args) -> str:
        app = str(args.get("app", "")).strip().lower()
        target = APP_ALIASES.get(app, app)
        try:
            if target.startswith("ms-settings:") or target.endswith(".exe"):
                os.startfile(target) if target.endswith(".exe") else os.system(f"start {target}")
            else:
                subprocess.Popen(target, shell=True)
        except Exception:
            os.system(f'start "" "{target}"')
        self.log("open_app", {"app": app})
        return f"Opened {app}, sir."

    def _close_app(self, args) -> str:
        app = str(args.get("app", "")).strip()
        name = app if app.lower().endswith(".exe") else f"{app}.exe"
        if not confirm_action(f"Close application '{app}' (force-terminate {name})"):
            self.log("close_app", {"app": app}, "denied")
            return f"Left {app} running."
        result = subprocess.run(["taskkill", "/IM", name, "/F"], capture_output=True, text=True)
        self.log("close_app", {"app": app})
        return f"Closed {app}." if result.returncode == 0 else f"Couldn't find {app} running."

    def _list_windows(self, args) -> str:
        import pyautogui  # lazy
        titles = [w.title for w in pyautogui.getAllWindows() if w.title.strip()]
        seen = list(dict.fromkeys(titles))[:20]
        self.log("list_windows")
        return "Open windows: " + ", ".join(seen) if seen else "No titled windows are open."

    def _set_volume(self, args) -> str:
        pct = max(0, min(100, int(args.get("percent", 50))))
        # nircmd if present, else a PowerShell keystroke fallback.
        if subprocess.run(["where", "nircmdc"], capture_output=True).returncode == 0:
            subprocess.run(["nircmdc", "setsysvolume", str(int(pct / 100 * 65535))])
        else:
            import pyautogui
            for _ in range(50):
                pyautogui.press("volumedown")
            for _ in range(int(pct / 2)):
                pyautogui.press("volumeup")
        self.log("set_volume", {"percent": pct})
        return f"Volume set to {pct}%."

    def _media_key(self, args) -> str:
        import pyautogui
        mapping = {"playpause": "playpause", "next": "nexttrack", "previous": "prevtrack",
                   "mute": "volumemute", "volumeup": "volumeup", "volumedown": "volumedown"}
        key = mapping.get(str(args.get("key", "")).lower())
        if not key:
            return "Unknown media key."
        pyautogui.press(key)
        self.log("media_key", {"key": key})
        return f"Pressed {args.get('key')}."

    def _hotkey(self, args) -> str:
        import pyautogui
        keys = [k.strip().lower() for k in str(args.get("combo", "")).split("+") if k.strip()]
        if not keys:
            return "No keys given."
        pyautogui.hotkey(*keys)
        self.log("hotkey", {"combo": "+".join(keys)})
        return f"Pressed {'+'.join(keys)}."

    def _power_action(self, args) -> str:
        action = str(args.get("action", "")).lower()
        cmds = {"lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
                "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                "restart": ["shutdown", "/r", "/t", "5"]}
        if action not in cmds:
            return "I can lock, sleep, or restart — which?"
        if not confirm_action(f"{action.capitalize()} the PC"):
            self.log("power_action", {"action": action}, "denied")
            return f"Cancelled the {action}."
        subprocess.Popen(cmds[action])
        self.log("power_action", {"action": action})
        return f"{action.capitalize()} initiated, sir." + (" (5-second delay)" if action == "restart" else "")
