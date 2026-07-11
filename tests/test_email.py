"""send_email (slice 11) — validation, classification, and the verbatim
CONFIRM block. The first primitive whose effect leaves this machine: every
invalid shape must die BEFORE the modal (nothing invalid is ever approvable),
and the modal must carry the literal to/subject/body/attachment-path — never
a model summary.

Stage 1 scope: pure classify/validation (no gate, no transport).
"""
from __future__ import annotations

import pytest

from jarvis.core.settings_store import settings
from jarvis.primitives import email as jemail
from jarvis.primitives import files as _files


@pytest.fixture()
def tmp_workspace(tmp_path, monkeypatch):
    """Isolate the attachment cage (same pattern as test_confirm_primitives)."""
    ws = tmp_path / "agent_files"
    ws.mkdir()
    monkeypatch.setattr(_files, "AGENT_FILES_DIR", ws)
    return ws


def classify(**kw):
    return jemail.classify_send_email(kw)


# ---------------------------------------------------------------- recipients

@pytest.mark.parametrize("bad_to", [
    "", "   ", "sam", "sam@", "@example.com", "sam@example",
    "sam @example.com", "sam@exa mple.com",
    "sam@example.com, bob@example.com",       # multi-recipient: v1 is single
    "sam@example.com bob@example.com",
    "sam@@example.com",
])
def test_malformed_address_blocked_before_modal(bad_to):
    info = classify(to=bad_to, subject="hi", body="hello")
    assert info["tier"] == "blocked"
    assert "command" not in info          # nothing approvable was built
    assert "BLOCKED" in info["description"]


@pytest.mark.parametrize("field,value", [
    ("to", "sam@example.com\r\nBcc: evil@example.com"),
    ("to", "sam@example.com\nBcc: evil@example.com"),
    ("subject", "invoice\r\nBcc: evil@example.com"),
    ("subject", "invoice\nX-Injected: 1"),
])
def test_crlf_header_injection_blocked(field, value):
    args = {"to": "sam@example.com", "subject": "invoice", "body": "hello"}
    args[field] = value
    info = jemail.classify_send_email(args)
    assert info["tier"] == "blocked"
    assert "command" not in info


# ---------------------------------------------------------------- attachments

def test_attachment_missing_blocked(tmp_workspace):
    info = classify(to="sam@example.com", subject="inv", body="see attached",
                    attachment="invoice.pdf")
    assert info["tier"] == "blocked"
    assert "invoice.pdf" in info["description"]
    assert "command" not in info


@pytest.mark.parametrize("escape", [
    "../../.env",                    # relative escape at the secrets
    "..\\..\\.env",
    "E:\\J.A.R.V.I.S\\.env",         # absolute path
    "C:\\Windows\\notepad.exe",
])
def test_attachment_escapes_cage_blocked(tmp_workspace, escape):
    info = classify(to="sam@example.com", subject="inv", body="b",
                    attachment=escape)
    assert info["tier"] == "blocked"
    assert "command" not in info


def test_attachment_directory_blocked(tmp_workspace):
    (tmp_workspace / "sub").mkdir()
    info = classify(to="sam@example.com", subject="inv", body="b",
                    attachment="sub")
    assert info["tier"] == "blocked"


def test_attachment_ok_inside_cage_confirms(tmp_workspace):
    f = tmp_workspace / "invoice.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    info = classify(to="sam@example.com", subject="inv", body="see attached",
                    attachment="invoice.pdf")
    assert info["tier"] == "confirm"
    # The modal names the EXACT resolved path + its size — the anti-exfil eyes.
    assert str(f.resolve()) in info["command"]
    assert str(len(b"%PDF-1.4 test")) in info["command"]
    assert info["attachment_path"] == str(f.resolve())


# ---------------------------------------------------------------- kill switch

def test_kill_switch_blocks():
    settings.set("email.enabled", False, persist=False)
    try:
        info = classify(to="sam@example.com", subject="hi", body="hello")
        assert info["tier"] == "blocked"
        assert "disabled" in info["description"].lower()
    finally:
        settings.set("email.enabled", True, persist=False)


# ---------------------------------------------------------------- content

def test_empty_message_blocked():
    info = classify(to="sam@example.com", subject="", body="")
    assert info["tier"] == "blocked"


def test_confirm_block_contains_verbatim_fields():
    to = "sam@example.com"
    subject = "Invoice from yesterday"
    body = "Hi Sam,\n\nplease find the invoice attached.\n\n-- sent by JARVIS"
    info = classify(to=to, subject=subject, body=body)
    assert info["tier"] == "confirm"
    block = info["command"]
    # Byte-for-byte: header lines and the FULL body, never truncated,
    # never paraphrased.
    assert f"To: {to}" in block
    assert f"Subject: {subject}" in block
    assert body in block
    # No model summary anywhere: the prose description must not restate the
    # body — it only tells the user to read the box.
    assert body not in info["description"]


def test_long_body_never_truncated():
    body = "line\n" * 5000                    # ~25KB — modal scrolls, we don't clip
    info = classify(to="sam@example.com", subject="s", body=body)
    assert info["tier"] == "confirm"
    assert body in info["command"]


# ======================================================================
# STAGE 2: registry + gate integration + transport (transport faked).
# The transport spy RAISES if touched on any non-approved path — a send
# that leaves this machine without an explicit approval is a hard failure.
# ======================================================================

import base64
import threading as _threading
import time as _time2
from email import message_from_bytes

from jarvis import primitives
from jarvis.core.confirmations import confirmations as _confirmations


def _auto_resolve(approved: bool):
    """Answer the next confirm_request after a beat (test_shell pattern)."""
    def responder(event):
        if event.get("type") == "confirm_request":
            _threading.Thread(target=lambda: (
                _time2.sleep(0.05),
                _confirmations.resolve(event["id"], approved))).start()
    return _confirmations.subscribe(responder)


@pytest.fixture()
def transport_boom(monkeypatch):
    """No test in this block may reach Gmail unless it explicitly re-patches."""
    def boom(raw):
        raise AssertionError("transport must NOT be touched on this path")
    monkeypatch.setattr(jemail, "_gmail_send", boom)


def test_send_email_registered_with_classify():
    assert "send_email" in primitives.PRIMITIVES
    assert primitives.PRIMITIVES["send_email"]["classify"] is jemail.classify_send_email


def test_disabled_hidden_from_schema_and_refused():
    from jarvis.brain import JarvisBrain
    settings.set("email.enabled", False, persist=False)
    try:
        names = [t["name"] for t in JarvisBrain().tools()]
        assert "send_email" not in names, "disabled send_email must not be advertised"
        out = primitives.execute("send_email", {"to": "sam@example.com",
                                                "subject": "hi", "body": "b"})
        assert out.startswith("BLOCKED") and "disabled" in out.lower()
    finally:
        settings.set("email.enabled", True, persist=False)
    assert "send_email" in [t["name"] for t in JarvisBrain().tools()]


def test_gate_carries_verbatim_block_to_modal(monkeypatch):
    """The confirm event's command field must be the literal block —
    to/subject/full body — not prose about it."""
    monkeypatch.setattr(jemail, "_gmail_send", lambda raw: {"id": "fake-id-1"})
    seen = {}
    unsub = _confirmations.subscribe(
        lambda e: seen.update(cmd=e.get("command"))
        if e.get("type") == "confirm_request" else None)
    approver = _auto_resolve(True)
    body = "Hi Sam,\nthe invoice is attached.\nBest"
    try:
        out = primitives.execute("send_email", {"to": "sam@example.com",
                                                "subject": "Invoice",
                                                "body": body})
    finally:
        approver()
        unsub()
    assert out.startswith("OK"), out
    assert "To: sam@example.com" in seen["cmd"]
    assert "Subject: Invoice" in seen["cmd"]
    assert body in seen["cmd"]


def test_decline_sends_nothing(transport_boom):
    unsub = _auto_resolve(False)
    try:
        out = primitives.execute("send_email", {"to": "sam@example.com",
                                                "subject": "hi", "body": "b"})
    finally:
        unsub()
    assert "CANCELLED" in out
    # transport_boom proves nothing left the machine


def test_timeout_sends_nothing(transport_boom):
    settings.set("confirm.timeout_s", 0.3, persist=False)
    try:
        out = primitives.execute("send_email", {"to": "sam@example.com",
                                                "subject": "hi", "body": "b"})
    finally:
        settings.set("confirm.timeout_s", 30, persist=False)
    assert "CANCELLED" in out


def test_no_approval_leak_across_chain(monkeypatch):
    """Two sends = two gates. An approval NEVER carries over to the next send."""
    sent = []
    monkeypatch.setattr(jemail, "_gmail_send",
                        lambda raw: sent.append(raw) or {"id": f"id-{len(sent)}"})
    approver = _auto_resolve(True)
    try:
        first = primitives.execute("send_email", {"to": "sam@example.com",
                                                  "subject": "one", "body": "b"})
    finally:
        approver()
    assert first.startswith("OK") and len(sent) == 1

    decliner = _auto_resolve(False)
    try:
        second = primitives.execute("send_email", {"to": "sam@example.com",
                                                   "subject": "two", "body": "b"})
    finally:
        decliner()
    assert "CANCELLED" in second
    assert len(sent) == 1, "the second send must NOT ride the first approval"


def test_send_reports_server_acceptance_honestly(monkeypatch):
    """Success = the server ACCEPTED the message (id reported). We never claim
    'delivered'. A transport error is a clean FAILED, never a raise."""
    monkeypatch.setattr(jemail, "_gmail_send", lambda raw: {"id": "abc123"})
    r = jemail.send_email_checked({"to": "sam@example.com",
                                   "subject": "s", "body": "b"})
    assert r["ok"] is True and r["id"] == "abc123"
    assert "accepted" in r["message"].lower()
    assert "deliver" not in r["message"].lower() or "cannot" in r["message"].lower()

    def explode(raw):
        raise RuntimeError("503 backend unavailable")
    monkeypatch.setattr(jemail, "_gmail_send", explode)
    r = jemail.send_email_checked({"to": "sam@example.com",
                                   "subject": "s", "body": "b"})
    assert r["ok"] is False
    assert "503" in r["message"]


def test_not_configured_gives_setup_message_never_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(jemail, "CRED_FILE", tmp_path / "credentials.json")
    monkeypatch.setattr(jemail, "TOKEN_FILE", tmp_path / "token.bin")
    r = jemail.send_email_checked({"to": "sam@example.com",
                                   "subject": "s", "body": "b"})
    assert r["ok"] is False
    assert "set up" in r["message"].lower() or "setup" in r["message"].lower()


def test_attachment_vanished_between_gate_and_send(tmp_workspace, transport_boom):
    """TOCTOU honesty: approved, but the file is gone by execution time —
    the runner re-validates and fails clean; nothing is sent."""
    f = tmp_workspace / "invoice.pdf"
    f.write_bytes(b"%PDF-1.4")

    def vanish_then_approve(event):
        if event.get("type") == "confirm_request":
            def go():
                _time2.sleep(0.05)
                f.unlink(missing_ok=True)          # gone between gate and run
                _confirmations.resolve(event["id"], True)
            _threading.Thread(target=go).start()
    unsub = _confirmations.subscribe(vanish_then_approve)
    try:
        out = primitives.execute("send_email", {"to": "sam@example.com",
                                                "subject": "inv", "body": "b",
                                                "attachment": "invoice.pdf"})
    finally:
        unsub()
    assert out.startswith("FAILED"), out
    assert "invoice.pdf" in out


def test_mime_payload_contains_attachment(tmp_workspace):
    """The raw payload really carries the file: headers + filename + bytes."""
    payload = b"%PDF-1.4 real-invoice-bytes"
    f = tmp_workspace / "invoice.pdf"
    f.write_bytes(payload)
    raw = jemail._build_mime("sam@example.com", "Invoice", "see attached",
                             str(f.resolve()))
    msg = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg["To"] == "sam@example.com"
    assert msg["Subject"] == "Invoice"
    parts = list(msg.walk())
    att = [p for p in parts if p.get_filename() == "invoice.pdf"]
    assert att, "attachment part missing"
    assert att[0].get_payload(decode=True) == payload
    texts = [p for p in parts if p.get_content_type() == "text/plain"]
    assert any("see attached" in p.get_payload(decode=True).decode() for p in texts)
