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
