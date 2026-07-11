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

import base64
import json
import mimetypes
import re
from pathlib import Path

from jarvis import config
from jarvis.core import dpapi
from jarvis.core.settings_store import settings
from jarvis.primitives import files

# OAuth artifacts live under data/ (gitignored). The token is DPAPI-encrypted
# at rest — same doctrine as memory: never write a credential in plaintext.
CRED_FILE = config.DATA_DIR / "email" / "credentials.json"
TOKEN_FILE = config.DATA_DIR / "email" / "token.bin"
# gmail.send ONLY: this credential can send mail and do nothing else —
# least privilege for the first outward-reaching verb.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

SETUP_HINT = (f"Gmail isn't set up yet. Put a Google OAuth client file at "
              f"{CRED_FILE} and run:  python -m jarvis.primitives.email  "
              f"(a one-time browser consent).")

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


# ---------------------------------------------------------------- sending

def send_email_checked(args: dict) -> dict:
    """Re-validate THEN send (the gate→run gap: files vanish, settings flip).
    Only a 'confirm' classification of the same args may reach the transport.
    Never raises. Returns {ok, id, message}."""
    try:
        info = _classify(args or {})
    except Exception as exc:
        return {"ok": False, "id": None,
                "message": f"could not re-validate the email: {exc}"}
    if info["tier"] != "confirm":
        return {"ok": False, "id": None, "message": info["description"]}

    to = str(args.get("to", "") or "").strip()
    subject = str(args.get("subject", "") or "")
    body = str(args.get("body", "") or "")
    attachment_path = info.get("attachment_path")
    try:
        raw = _build_mime(to, subject, body, attachment_path)
    except Exception as exc:
        return {"ok": False, "id": None,
                "message": f"couldn't build the message: {exc}"}
    try:
        resp = _gmail_send(raw)
    except EmailNotConfigured as exc:
        return {"ok": False, "id": None, "message": str(exc)}
    except Exception as exc:
        return {"ok": False, "id": None,
                "message": f"the send failed: {exc}"}
    mid = (resp or {}).get("id", "")
    att = f", attachment {attachment_path}" if attachment_path else ""
    # HONESTY: accepted-by-server is all we can verify with a send-only
    # scope. Never claim delivered or read.
    return {"ok": True, "id": mid,
            "message": f"email to {to} accepted by the mail server "
                       f"(message id {mid}{att}). Accepted means handed off — "
                       f"I cannot verify delivery."}


def _build_mime(to: str, subject: str, body: str,
                attachment_path: str | None = None) -> str:
    """The raw base64url RFC-2822 message. 'From' is left to Gmail — the
    authenticated account is the sender, not whatever a header claims."""
    from email.message import EmailMessage  # stdlib (absolute import)
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment_path:
        p = Path(attachment_path)
        ctype, _ = mimetypes.guess_type(p.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype,
                           filename=p.name)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


class EmailNotConfigured(RuntimeError):
    """No usable OAuth token. Runtime never opens a consent browser mid-chain
    — it fails clean and points at the one-time setup command."""


def _load_token():
    """Credentials from the DPAPI blob, or None (missing/corrupt/foreign —
    honest degradation, never a crash)."""
    if not TOKEN_FILE.exists():
        return None
    try:
        raw = dpapi.unprotect(TOKEN_FILE.read_bytes())
        from google.oauth2.credentials import Credentials
        return Credentials.from_authorized_user_info(json.loads(raw),
                                                     GMAIL_SCOPES)
    except Exception:
        return None


def _save_token(creds) -> None:
    """DPAPI or nothing — a plaintext mail credential never touches disk."""
    if not dpapi.available():
        return
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_bytes(dpapi.protect(creds.to_json().encode("utf-8")))


def _gmail_send(raw: str) -> dict:
    """Hand the raw message to Gmail. Returns the API response ({'id': ...}).
    Raises EmailNotConfigured without a usable token; raises transport errors
    (send_email_checked turns both into honest FAILED strings)."""
    creds = _load_token()
    if creds and creds.expired and creds.refresh_token:
        try:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            _save_token(creds)
        except Exception:
            creds = None
    if not creds or not creds.valid:
        raise EmailNotConfigured(SETUP_HINT)
    from googleapiclient.discovery import build
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return service.users().messages().send(userId="me",
                                           body={"raw": raw}).execute()


def setup_oauth() -> bool:
    """One-time interactive consent (python -m jarvis.primitives.email).
    Deliberately NOT reachable from the agent loop."""
    if not CRED_FILE.exists():
        print(f"Missing {CRED_FILE} — download an OAuth client (Desktop app) "
              f"from Google Cloud Console and place it there first.")
        return False
    if not dpapi.available():
        print("Refusing: DPAPI is unavailable, the token would be plaintext.")
        return False
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(CRED_FILE),
                                                     GMAIL_SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    print(f"Token stored (DPAPI-encrypted) at {TOKEN_FILE}. send_email is ready.")
    return True


if __name__ == "__main__":
    setup_oauth()
