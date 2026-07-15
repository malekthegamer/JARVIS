"""Persistent audit log (slice 18) — spec §1.4's "every action recorded".

The HUD Action Log is a live glance view that dies with the session; this is
the durable record underneath it. One JSONL line per action, split-record
privacy (user-approved):

  envelope  (plaintext) — ts, chain, tool, tier, gate, status, dry_run:
             the timeline stays grep-able/tail-able with no tooling.
  payload   (DPAPI via "enc") — args (verbatim, untruncated) + result
             (capped): the sensitive content is protected at rest exactly
             like data/memory/memories.bin.

Failure posture (reasoned in the slice-18 plan):
  * DPAPI failing at write time -> the envelope is STILL written with
    enc=null + payload_error — the timeline is never lost, and sensitive
    args are never written plaintext (slice-10 refusal doctrine, per-field).
  * record() NEVER raises; it returns False so the caller can surface the
    gap loudly (execute() appends a warning to the tool result).
  * Rotation at audit.max_file_mb renames the file aside; rotated files are
    never auto-deleted — an audit log that deletes itself isn't one.

Dump CLI (a decrypt utility, not a browsing UI):
    python -m jarvis.core.audit [--tail N] [--file PATH]
"""
from __future__ import annotations

import base64
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from jarvis import config
from jarvis.core import dpapi

DEFAULT_PATH = config.DATA_DIR / "audit" / "audit.jsonl"


class AuditLog:
    """Append-only JSONL audit log. All writes are serialized by one lock;
    a corrupt line damages only itself (unlike a whole-file store)."""

    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    # ---------- writing ----------

    def record(self, *, tool: str, tier: str, status: str,
               gate: str | None = None, dry_run: bool = False,
               chain_id: str | None = None, args: dict | None = None,
               result: str = "") -> bool:
        """Write one audit line. Never raises; False = not durably recorded
        (the caller is expected to say so loudly)."""
        try:
            from jarvis.core.settings_store import settings
            if not settings.get("audit.enabled", True):
                return True  # deliberately off — not a write failure
            max_chars = int(settings.get("audit.result_max_chars", 2000))
            envelope = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "chain": chain_id,
                "tool": str(tool),
                "tier": str(tier),
                "gate": gate,
                "status": str(status),
                "dry_run": bool(dry_run),
            }
            envelope.update(self._protect_payload(args or {}, str(result),
                                                  max_chars))
            line = json.dumps(envelope, ensure_ascii=False)
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed(settings)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            return True
        except Exception:
            return False

    @staticmethod
    def _protect_payload(args: dict, result: str, max_chars: int) -> dict:
        """DPAPI-encrypt {args, result}. Degrades to enc=null + an error
        marker — the sensitive content is dropped, NEVER written plaintext."""
        payload = {
            "args": args,
            "result": result[:max_chars],
            "result_len": len(result),
            "truncated": len(result) > max_chars,
        }
        try:
            blob = dpapi.protect(json.dumps(payload, ensure_ascii=False)
                                 .encode("utf-8"))
            return {"enc": base64.b64encode(blob).decode("ascii")}
        except Exception:
            return {"enc": None, "payload_error": "encryption unavailable"}

    def _rotate_if_needed(self, settings) -> None:
        """Called under the lock, before an append. At/over the size cap the
        current file is renamed aside (never deleted) and a fresh one starts."""
        max_mb = float(settings.get("audit.max_file_mb", 5))
        try:
            size = self.path.stat().st_size
        except OSError:
            return  # no file yet
        if size < max_mb * 1024 * 1024:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"audit-{stamp}.jsonl")
        n = 1
        while target.exists():
            target = self.path.with_name(f"audit-{stamp}-{n}.jsonl")
            n += 1
        self.path.rename(target)

    # ---------- reading ----------

    def read(self, path: Path | str | None = None) -> list[dict]:
        """Read records (envelope + decrypted payload where possible).
        Honest degradation: a missing file is [], a corrupt line is skipped,
        an undecryptable payload comes back as payload=None + payload_error —
        the plaintext envelope is always returned."""
        p = Path(path) if path else self.path
        records: list[dict] = []
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            return records
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                env = json.loads(line)
            except Exception:
                continue  # a corrupt line damages only itself
            if not isinstance(env, dict):
                continue
            enc = env.pop("enc", None)
            if enc is None:
                env.setdefault("payload_error", "encryption unavailable")
                env["payload"] = None
            else:
                try:
                    raw = dpapi.unprotect(base64.b64decode(enc))
                    env["payload"] = json.loads(raw.decode("utf-8"))
                except Exception:
                    env["payload"] = None
                    env["payload_error"] = "payload could not be decrypted"
            records.append(env)
        return records


# One process-wide log. Splices must reach it via the module attribute
# (audit.audit_log), never a from-import, so tests can swap it per-test.
audit_log = AuditLog()


def _main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Decrypt-dump the JARVIS audit log (read-only).")
    parser.add_argument("--tail", type=int, default=0,
                        help="show only the last N records")
    parser.add_argument("--file", type=Path, default=None,
                        help="a specific (e.g. rotated) audit file")
    ns = parser.parse_args()
    records = AuditLog(ns.file or DEFAULT_PATH).read()
    if ns.tail > 0:
        records = records[-ns.tail:]
    for rec in records:
        print(json.dumps(rec, ensure_ascii=False))
    if not records:
        print("(no audit records)")


if __name__ == "__main__":
    _main()
