"""Media & entertainment — playback control via media keys (works with Spotify,
YouTube, VLC, etc. without any API key)."""
from __future__ import annotations

from core.skill_registry import register_skill
from skills.base import Skill, prop, tool


@register_skill
class MediaControlSkill(Skill):
    name = "media_control"
    description = "Control media playback and volume (play/pause, next, previous, volume) for any app."

    def tools(self) -> list[dict]:
        return [
            tool("playback", "Control media playback with a media key.",
                 {"command": prop("string", "One of: playpause, play, pause, next, previous, stop")}, ["command"]),
            tool("volume", "Adjust volume with media keys.",
                 {"direction": prop("string", "One of: up, down, mute"),
                  "steps": prop("integer", "How many steps (default 5)")}, ["direction"]),
        ]

    def execute(self, tool: str, args: dict) -> str:
        try:
            import pyautogui  # lazy
            if tool == "playback":
                cmd = str(args.get("command", "")).lower()
                key = {"playpause": "playpause", "play": "playpause", "pause": "playpause",
                       "next": "nexttrack", "previous": "prevtrack", "stop": "stop"}.get(cmd)
                if not key:
                    return "Try playpause, next, previous, or stop."
                pyautogui.press(key)
                self.log("playback", {"command": cmd})
                return f"{cmd.capitalize()}."
            if tool == "volume":
                direction = str(args.get("direction", "")).lower()
                if direction == "mute":
                    pyautogui.press("volumemute")
                    self.log("volume", {"direction": "mute"})
                    return "Muted."
                key = "volumeup" if direction == "up" else "volumedown"
                for _ in range(int(args.get("steps", 5))):
                    pyautogui.press(key)
                self.log("volume", {"direction": direction})
                return f"Volume {direction}."
        except Exception as exc:
            return f"Media control failed: {exc}"
        return f"Unknown media tool {tool}."
