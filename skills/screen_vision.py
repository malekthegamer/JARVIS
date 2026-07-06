"""Vision & screen understanding — screenshot + OCR to read the screen, and
OCR-guided clicking for 'click the X button' style commands.

Needs the Tesseract binary installed (pytesseract is just the wrapper). If it's
missing, the tools say so clearly instead of crashing."""
from __future__ import annotations

from core.skill_registry import register_skill
from skills.base import Skill, prop, tool


@register_skill
class ScreenVisionSkill(Skill):
    name = "screen_vision"
    description = "Read text currently on screen via OCR, and click on-screen text/buttons by name."

    def tools(self) -> list[dict]:
        return [
            tool("read_screen", "Capture the screen and OCR its text so you can answer questions about what's shown."),
            tool("click_text", "Find on-screen text via OCR and click it (for 'click the X button').",
                 {"text": prop("string", "The visible text/label to click")}, ["text"]),
        ]

    def execute(self, tool: str, args: dict) -> str:
        try:
            if tool == "read_screen":
                return self._read_screen()
            if tool == "click_text":
                return self._click_text(str(args.get("text", "")))
        except Exception as exc:
            return f"Vision failed (is Tesseract installed?): {exc}"
        return f"Unknown vision tool {tool}."

    def _grab(self):
        import mss
        from PIL import Image
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def _read_screen(self) -> str:
        import pytesseract
        text = pytesseract.image_to_string(self._grab())
        self.log("read_screen")
        clean = " ".join(text.split())
        return f"On-screen text:\n{clean[:4000]}" if clean else "No readable text found on screen."

    def _click_text(self, target: str) -> str:
        import pyautogui
        import pytesseract
        img = self._grab()
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        for i, word in enumerate(data["text"]):
            if target.lower() in word.lower() and word.strip():
                x = data["left"][i] + data["width"][i] // 2
                y = data["top"][i] + data["height"][i] // 2
                pyautogui.click(x, y)
                self.log("click_text", {"text": target})
                return f"Clicked '{word}' at ({x}, {y})."
        return f"Couldn't find '{target}' on screen."
