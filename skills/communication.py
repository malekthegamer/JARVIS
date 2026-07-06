"""Communication — email (Gmail API, OAuth) and optional Discord.

Sending ALWAYS confirms the final draft first. Both integrations are optional
seams: with no credentials configured they report how to set up rather than
crash. SMS/Twilio is intentionally left out (paid, not a dependency)."""
from __future__ import annotations

from pathlib import Path

import config
from core.confirmations import confirm_action
from core.skill_registry import register_skill
from skills.base import Skill, prop, tool

GMAIL_CREDS = config.BASE_DIR / "credentials.json"
GMAIL_TOKEN = config.BASE_DIR / "token.json"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


@register_skill
class CommunicationSkill(Skill):
    name = "communication"
    description = "Draft and send email via Gmail (always confirmed before sending)."

    def tools(self) -> list[dict]:
        return [
            tool("send_email", "Draft an email and send it AFTER the user confirms the draft.",
                 {"to": prop("string", "Recipient address"),
                  "subject": prop("string", "Subject line"),
                  "body": prop("string", "Email body")}, ["to", "subject", "body"]),
        ]

    def execute(self, tool: str, args: dict) -> str:
        if tool == "send_email":
            return self._send_email(args)
        return f"Unknown communication tool {tool}."

    def _gmail_service(self):
        if not GMAIL_CREDS.exists():
            raise RuntimeError("Gmail isn't set up. Place OAuth credentials.json in the JARVIS folder.")
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if GMAIL_TOKEN.exists():
            creds = Credentials.from_authorized_user_file(str(GMAIL_TOKEN), GMAIL_SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(GMAIL_CREDS), GMAIL_SCOPES)
                creds = flow.run_local_server(port=0)
            GMAIL_TOKEN.write_text(creds.to_json(), encoding="utf-8")
        return build("gmail", "v1", credentials=creds)

    def _send_email(self, args) -> str:
        to, subject, body = args.get("to", ""), args.get("subject", ""), args.get("body", "")
        preview = f"To: {to}\nSubject: {subject}\n\n{body}"
        if not confirm_action(f"Send this email:\n{preview}"):
            self.log("send_email", {"to": to}, "denied")
            return "Email not sent — draft cancelled."
        try:
            import base64
            from email.mime.text import MIMEText
            service = self._gmail_service()
            msg = MIMEText(body)
            msg["to"], msg["subject"] = to, subject
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            self.log("send_email", {"to": to})
            return f"Email sent to {to}, sir."
        except Exception as exc:
            return f"Couldn't send: {exc}"
