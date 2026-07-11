"""Slice-11 Stage-4 live tests: REAL Gmail sends.

BINDING approval condition (user-added, recorded in the plan): live sends go
ONLY to the user's own address, read from TEST_SELF_EMAIL in .env — never a
literal address in committed source, never a third party. Every path that
could reach the transport hard-asserts the recipient equals that value; the
chain test's auto-approver DECLINES any modal whose To: line differs.

Gated on TEST_SELF_EMAIL + the OAuth token (and Gemini for the chain test);
on the configured dev machine they run (0 skipped).
"""
from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

from jarvis import config
from jarvis.core import chain
from jarvis.core.confirmations import confirmations
from jarvis.primitives import email as jemail
from jarvis.primitives import files
from jarvis.state import AgentState, broadcaster


def _self_email() -> str:
    # config import above loaded .env into the environment
    return os.environ.get("TEST_SELF_EMAIL", "").strip()


email_live = pytest.mark.skipif(
    not (_self_email() and jemail.TOKEN_FILE.exists()),
    reason="live email tests need TEST_SELF_EMAIL in .env and a Gmail OAuth token")


def _guard_recipient(to: str) -> None:
    """The hard line: no live test may address anyone but the user."""
    assert to and to == _self_email(), \
        "live tests may ONLY send to TEST_SELF_EMAIL — refusing"
    assert jemail.valid_address(to), f"TEST_SELF_EMAIL is not a valid address: {to!r}"


@pytest.fixture()
def cage_invoice():
    """A real invoice PDF in the real workspace, mtime = yesterday (so
    'saved yesterday' + within_days search both hold). Cleaned up after."""
    files.AGENT_FILES_DIR.mkdir(parents=True, exist_ok=True)
    name = f"invoice-{uuid.uuid4().hex[:8]}.pdf"
    f = files.AGENT_FILES_DIR / name
    f.write_bytes(b"%PDF-1.4\nJARVIS slice-11 live invoice\n%%EOF")
    yesterday = time.time() - 86400
    os.utime(f, (yesterday, yesterday))
    yield name, f
    f.unlink(missing_ok=True)


@email_live
def test_live_send_to_self_accepted(cage_invoice):
    """Transport-level proof: a real message with a real attachment is
    ACCEPTED by Gmail (message id returned). The gate itself is proven
    offline in test_email.py; this exercises the true API path."""
    to = _self_email()
    _guard_recipient(to)
    name, _f = cage_invoice
    r = jemail.send_email_checked({
        "to": to,
        "subject": f"JARVIS slice-11 live test {uuid.uuid4().hex[:8]}",
        "body": "This is JARVIS verifying the live Gmail send path.\n"
                "Sent to self by the slice-11 acceptance tests.",
        "attachment": name,
    })
    assert r["ok"] is True, r
    assert r["id"], r
    assert "accepted" in r["message"].lower(), r
    print(f"[live] send-to-self accepted, id={r['id']}")


@pytest.fixture()
def event_log():
    events: list[dict] = []
    unsubscribe = broadcaster.subscribe(events.append)
    yield events
    unsubscribe()
    assert chain.current() is None, "a live test leaked a ChainTracker"
    assert broadcaster.current is AgentState.IDLE


@email_live
@pytest.mark.skipif(not config.get_api_key("gemini"),
                    reason="GEMINI_API_KEY not configured")
def test_live_script3_invoice_chain(cage_invoice, event_log):
    """Spec §1.6 script #3, end to end with the REAL model and REAL Gmail:
    'find the invoice PDF I saved yesterday and email it to Sam' — Sam is
    played by TEST_SELF_EMAIL. Verified mechanically: the CONFIRM modal's
    verbatim block named the right recipient AND the exact attachment path,
    and the tool result reports the server-accepted message id — evidence
    the executor collected, not prose the model asserted."""
    from jarvis.brain import JarvisBrain

    to = _self_email()
    _guard_recipient(to)
    name, invoice_path = cage_invoice

    seen_blocks: list[str] = []

    def approver(event):
        if event.get("type") != "confirm_request":
            return
        block = event.get("command") or ""
        seen_blocks.append(block)
        # Approve ONLY a send addressed to the user; anything else declines.
        ok = f"To: {to}" in block
        threading.Thread(target=lambda: (
            time.sleep(0.1),
            confirmations.resolve(event["id"], ok))).start()

    unsub = confirmations.subscribe(approver)
    try:
        brain = JarvisBrain()
        reply = brain.think(
            f"Find the invoice PDF I saved yesterday in your workspace and "
            f"email it to Sam at {to} with a short polite message.")

        send_result = next((m["content"] for m in reversed(brain.history)
                            if m["role"] == "tool" and m["name"] == "send_email"),
                           None)
        assert send_result is not None, \
            f"model never called send_email; reply: {reply[:200]}"
        assert send_result.startswith("OK:"), send_result
        assert "accepted" in send_result.lower(), send_result
        assert "message id" in send_result.lower(), send_result

        assert seen_blocks, "send_email must have passed the CONFIRM gate"
        block = seen_blocks[-1]
        assert f"To: {to}" in block, block
        assert str(invoice_path.resolve()) in block, \
            f"modal must name the exact attachment path; block: {block}"

        # The send is fully verified above (OK + accepted id + correct block).
        # The chain then makes ONE more live generate() call for the model's
        # closing line; a transient provider error there ends the chain 'error'
        # AFTER the email already went — it does not un-send it. So accept a
        # terminal chain_end of 'done' OR 'error' (same doctrine as
        # test_chain_live's bounded-terminal-state check). A missing chain_end,
        # or an aborted status like 'budget'/'cancelled', still fails.
        ends = [e for e in event_log if e["type"] == "chain_end"]
        assert ends and ends[-1]["status"] in ("done", "error"), ends
        print(f"[live] script #3: confirmed block ok, result: {send_result[:160]}")
        print(f"[live] reply: {reply[:160]}")
    finally:
        unsub()
