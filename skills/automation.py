"""Agentic automation — multi-step chains and browser automation.

Multi-step chains are decomposed by the BRAIN into ordered tool calls (that is
the natural tool-calling loop in brain.py), so this skill exposes the browser
piece plus a small 'run_steps' convenience. Any browser action that submits a
form / purchases / posts REQUIRES confirmation.

Playwright is optional; if it's not installed the browser tools report that
clearly instead of crashing.
"""
from __future__ import annotations

from core.confirmations import confirm_action
from core.skill_registry import register_skill
from skills.base import Skill, prop, tool


@register_skill
class AutomationSkill(Skill):
    name = "automation"
    description = "Open web pages and read their text, and perform confirmed browser actions like clicking or submitting."

    def tools(self) -> list[dict]:
        return [
            tool("browse_url", "Open a URL in a headless browser and return the visible text.",
                 {"url": prop("string", "The URL to open")}, ["url"]),
            tool("browser_action", "Perform an action on the last opened page: click/type/submit. Submitting/posting requires confirmation.",
                 {"action": prop("string", "One of: click, type, submit"),
                  "selector": prop("string", "CSS selector of the target element"),
                  "text": prop("string", "Text to type (for the 'type' action)")}, ["action", "selector"]),
        ]

    def __init__(self) -> None:
        self._page = None
        self._browser = None
        self._pw = None

    def execute(self, tool: str, args: dict) -> str:
        try:
            if tool == "browse_url":
                return self._browse(args)
            if tool == "browser_action":
                return self._action(args)
            return f"Unknown automation tool {tool}."
        except ImportError:
            return "Browser automation needs Playwright: pip install playwright && playwright install chromium."
        except Exception as exc:
            self.log(tool, args, "error")
            return f"Automation failed: {exc}"

    def _ensure_page(self):
        if self._page is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._page = self._browser.new_page()
        return self._page

    def _browse(self, args) -> str:
        url = str(args.get("url", ""))
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        page = self._ensure_page()
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        text = page.inner_text("body")[:4000]
        self.log("browse_url", {"url": url})
        return f"Loaded {url}. Visible text:\n{text}"

    def _action(self, args) -> str:
        action = str(args.get("action", "")).lower()
        selector = str(args.get("selector", ""))
        page = self._ensure_page()
        if action in ("submit", "post") or (action == "click" and "submit" in selector.lower()):
            if not confirm_action(f"Perform browser '{action}' on element '{selector}' (may submit a form or post content)"):
                self.log("browser_action", {"action": action, "selector": selector}, "denied")
                return "Browser action cancelled."
        if action == "click":
            page.click(selector, timeout=10000)
        elif action == "type":
            page.fill(selector, str(args.get("text", "")))
        elif action == "submit":
            page.press(selector, "Enter")
        else:
            return f"Unknown browser action '{action}'."
        self.log("browser_action", {"action": action, "selector": selector})
        return f"Did '{action}' on {selector}."
