"""Slice-10 Stage-4: memory persists across a REAL process restart. Two
separate Python processes — one writes, a fresh one reads — so this proves
durability across the app being closed and reopened, not just a new object."""
from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest


def test_memory_persists_across_process_restart(tmp_path):
    store = tmp_path / "restart" / "memories.bin"
    marker = "I keep my spare key under the third flowerpot"

    writer = textwrap.dedent(f"""
        import sys; sys.path.insert(0, r"{sys.path[0] or '.'}")
        from jarvis.core.memory import MemoryStore
        MemoryStore(r"{store}").add({marker!r})
        print("WROTE")
    """)
    r1 = subprocess.run([sys.executable, "-c", writer],
                        capture_output=True, text=True, cwd=r"e:\J.A.R.V.I.S")
    assert "WROTE" in r1.stdout, r1.stderr

    reader = textwrap.dedent(f"""
        import sys; sys.path.insert(0, r"{sys.path[0] or '.'}")
        from jarvis.core.memory import MemoryStore
        texts = [r["text"] for r in MemoryStore(r"{store}").all()]
        print("FOUND" if any("flowerpot" in t for t in texts) else "MISSING")
    """)
    r2 = subprocess.run([sys.executable, "-c", reader],
                        capture_output=True, text=True, cwd=r"e:\J.A.R.V.I.S")
    assert "FOUND" in r2.stdout, (r2.stdout, r2.stderr)

    # and it is encrypted at rest — the plaintext never touches disk
    assert b"flowerpot" not in store.read_bytes()


def test_live_remember_recall_and_no_pollution(tmp_path):
    """The real Gemini brain: it STORES on explicit request, a fresh brain
    reading the persisted file RECALLS it, and an unrelated question surfaces
    NOTHING. Uses a temp store so the user's real memory is untouched."""
    from jarvis import config
    if not config.get_api_key("gemini"):
        pytest.skip("GEMINI_API_KEY not configured")
    from jarvis.brain import JarvisBrain
    from jarvis.core.memory import MemoryStore
    path = tmp_path / "live.bin"

    b1 = JarvisBrain()
    b1.memory = MemoryStore(path)
    b1.think("Please remember that I take my coffee black with no sugar.")
    stored = MemoryStore(path).all()
    assert any("coffee" in r["text"].lower() for r in stored), \
        f"model should have stored the fact; store={stored}"

    # fresh brain + fresh store reading the persisted file == a restart
    b2 = JarvisBrain()
    b2.memory = MemoryStore(path)
    reply = b2.think("How do I take my coffee?")
    assert "black" in reply.lower(), reply

    # unrelated query must NOT surface the coffee fact
    b3 = JarvisBrain()
    b3.memory = MemoryStore(path)
    reply2 = b3.think("What is the capital of France?")
    assert "coffee" not in reply2.lower(), reply2

    assert b"black" not in path.read_bytes()  # encrypted at rest
