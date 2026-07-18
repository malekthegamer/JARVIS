"""Slice 15 Stage 3 — LIVE web-search acceptance (gated on GEMINI_API_KEY).

The REAL Gemini brain uses the REAL keyless ddgs search: (1) it can search and
answer a question; (2) it can chain search → browse_navigate → read_page for a
fuller answer. Hits the live network (ddgs + a real site) — that's the nature of
a live web test.
"""
from __future__ import annotations

import pytest

from jarvis import config
from jarvis.core.settings_store import settings

live = pytest.mark.skipif(not config.get_api_key("gemini"),
                          reason="GEMINI_API_KEY not configured")


@pytest.fixture(autouse=True, scope="module")
def _web_settings():
    settings.set("web.headless", True, persist=False)
    settings.set("web.profile_mode", "isolated", persist=False)  # pin (machine may have real on)
    settings.set("web.allow_actions", False, persist=False)
    yield
    from jarvis.primitives import web
    web.session.close()
    settings.set("web.headless", False, persist=False)


@live
def test_live_search_answers_question():
    from jarvis.brain import JarvisBrain
    brain = JarvisBrain()
    reply = brain.think(
        "Search the web to find out what the capital of Australia is, and tell me.")
    tools = [m["name"] for m in brain.history if m.get("role") == "tool"]
    assert "web_search" in tools, f"never searched; tools={tools}"
    assert "canberra" in reply.lower(), f"reply didn't answer from search: {reply[:200]}"
    print(f"[live] search answer: {reply[:160]}")


@live
def test_live_search_then_read_chain():
    """Deeper question: search → open a result → read it. Exercises the slice-14
    chain the model orchestrates itself."""
    from jarvis.brain import JarvisBrain
    brain = JarvisBrain()
    reply = brain.think(
        "Search for the official Python programming language website, then OPEN "
        "it in your browser and READ the page, and tell me in one sentence what "
        "Python is described as.")
    tools = [m["name"] for m in brain.history if m.get("role") == "tool"]
    assert "web_search" in tools, f"never searched; tools={tools}"
    assert "browse_navigate" in tools, f"never opened a result; tools={tools}"
    assert "read_page" in tools, f"never read the page; tools={tools}"
    assert "python" in reply.lower(), f"reply didn't reflect the page: {reply[:200]}"
    print(f"[live] search->read chain reply: {reply[:180]}")
    print(f"[live] tool order: {tools}")
