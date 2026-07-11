"""send_email — compose + send one email (spec §1.6 script #3). Slice 11.

The first primitive whose effect leaves this machine and reaches another
person, and it is irreversible once the server accepts it. Controls, in order:

  1. VALIDATION (this module, before any modal): kill switch, a single
     RFC-plausible recipient, CR/LF header-injection refusal, and an
     attachment cage — the attachment must resolve inside data/agent_files/
     (same two-belt containment as delete_file). Anything invalid is BLOCKED
     outright: nothing invalid is ever approvable.
  2. CONFIRM (existing gate): the modal's monospace box shows the VERBATIM
     To / Subject / exact attachment path / full body — never a model
     summary. A summary by the same (possibly wrong or injected) model that
     composed the email is anti-safety.
  3. Send (stage 2): Gmail API, OAuth, gmail.send scope only. Success is the
     server ACCEPTING the message — we never claim "delivered" or "read".

Send-only, single recipient, one optional attachment. Inbox reading is
deliberately out of scope (a much larger privacy surface — its own slice).
"""
from __future__ import annotations

import re

from jarvis.core.settings_store import settings
from jarvis.primitives import files

# Single address, no whitespace/commas/semicolons, one @, dotted domain.
# Deliberately strict: one recipient in v1 — a mistake's blast radius is one.
_ADDRESS_RE = re.compile(r"^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$")

_RULE = "────────"  # separates verbatim headers from the verbatim body


def valid_address(addr: str) -> bool:
    return bool(_ADDRESS_RE.match(addr))


def _has_crlf(*values: str) -> bool:
    return any("\r" in v or "\n" in v for v in values)


def classify_send_email(args: dict) -> dict:
    """Dynamic tier for send_email. Never raises. Returns one of:
      blocked — kill switch off, malformed/injected recipient or subject,
                empty message, or an attachment that is missing or outside
                the cage. Refused before any modal exists.
      confirm — carrying the verbatim message block for the modal
                ('command' field → the HUD's monospace box) plus the
                resolved 'attachment_path' for the runner.
    """
    try:
        return _classify(args or {})
    except Exception as exc:
        # Fail closed with a refusal, never an approvable-but-unvalidated modal.
        return {"tier": "blocked",
                "description": f"BLOCKED: could not validate the email ({exc})."}


def _classify(args: dict) -> dict:
    if not settings.get("email.enabled", True):
        return {"tier": "blocked",
                "description": "BLOCKED: email sending is disabled in settings."}

    to = str(args.get("to", "") or "").strip()
    subject = str(args.get("subject", "") or "")
    body = str(args.get("body", "") or "")

    if _has_crlf(to, subject):
        return {"tier": "blocked",
                "description": "BLOCKED: the recipient or subject contains a "
                               "line break — that is how mail headers get "
                               "injected, so I refuse it outright."}
    if not valid_address(to):
        return {"tier": "blocked",
                "description": f"BLOCKED: '{to}' is not a single valid email "
                               f"address. Give me exactly one address like "
                               f"name@example.com."}
    if not subject.strip() and not body.strip():
        return {"tier": "blocked",
                "description": "BLOCKED: both the subject and the body are "
                               "empty — there is nothing to send."}

    attachment = str(args.get("attachment", "") or "").strip()
    attachment_path = None
    attachment_line = ""
    if attachment:
        target = files._contained(attachment)
        if target is None:
            return {"tier": "blocked",
                    "description": f"BLOCKED: attachment '{attachment}' is "
                                   f"outside my workspace "
                                   f"({files.AGENT_FILES_DIR}). I only attach "
                                   f"files from in there."}
        if not target.exists():
            return {"tier": "blocked",
                    "description": f"BLOCKED: no file named '{attachment}' in "
                                   f"my workspace — nothing to attach."}
        if target.is_dir():
            return {"tier": "blocked",
                    "description": f"BLOCKED: '{attachment}' is a folder — I "
                                   f"only attach files."}
        size = target.stat().st_size
        attachment_path = str(target)
        attachment_line = f"Attachment: {attachment_path} ({size:,} bytes)\n"

    # The verbatim block — mechanically assembled from the literal args.
    # Full body, never truncated: truncation would defeat "verbatim".
    block = (f"To: {to}\n"
             f"Subject: {subject}\n"
             f"{attachment_line}"
             f"{_RULE}\n"
             f"{body}")
    return {
        "tier": "confirm",
        "command": block,
        "attachment_path": attachment_path,
        "description": (f"Send an email to {to} via Gmail. Review the exact "
                        f"message shown — it will be sent as-is and cannot be "
                        f"recalled once the server accepts it."),
    }
