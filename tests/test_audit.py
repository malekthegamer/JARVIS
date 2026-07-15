"""Slice 18 — persistent audit log (spec §1.4 "every action recorded").

Stage 1 covers the AuditLog store itself: append-only JSONL, split-record
privacy (plaintext envelope + DPAPI payload), durability across restart,
truncation, rotation, and honest degradation when DPAPI is unavailable.
Stage 2 adds the execute()/brain splice tests (gate outcomes, blocked,
unknown tool, write-failure posture, meta-tool mutations).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis.core import audit, dpapi
from jarvis.core.settings_store import settings as _settings

SENTINEL = "TOP-SECRET-AUDIT-BODY-93731"


@pytest.fixture()
def log_path(tmp_path) -> Path:
    return tmp_path / "audit" / "audit.jsonl"


@pytest.fixture()
def log(log_path) -> audit.AuditLog:
    return audit.AuditLog(log_path)


def _raw(log_path: Path) -> str:
    return log_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- test 1
def test_record_appends_jsonl_lines(log, log_path):
    assert log.record(tool="launch_app", tier="auto", status="ok",
                      args={"name": "notepad"}, result="OK: launched.")
    assert log.record(tool="delete_file", tier="confirm", status="cancelled",
                      gate="declined", args={"name": "a.txt"},
                      result="CANCELLED (the user declined): ...")
    lines = _raw(log_path).splitlines()
    assert len(lines) == 2
    first, second = (json.loads(l) for l in lines)
    # Envelope fields present and plaintext-readable.
    for env in (first, second):
        for key in ("ts", "chain", "tool", "tier", "gate", "status",
                    "dry_run", "enc"):
            assert key in env, f"envelope missing {key}"
    assert first["tool"] == "launch_app" and first["gate"] is None
    assert second["gate"] == "declined" and second["status"] == "cancelled"
    assert first["dry_run"] is False


# ---------------------------------------------------------------- test 2
def test_envelope_plaintext_payload_encrypted(log, log_path):
    if not dpapi.available():
        pytest.fail("DPAPI must be available on this machine (pywin32)")
    log.record(tool="send_email", tier="confirm", status="ok", gate="approved",
               args={"to": "sam@example.com", "body": SENTINEL},
               result=f"OK: accepted. body was {SENTINEL}")
    raw = _raw(log_path)
    assert "send_email" in raw            # timeline is grep-able
    assert SENTINEL not in raw            # content is NOT plaintext on disk
    # The reader decrypts the payload back.
    (rec,) = log.read()
    assert rec["payload"]["args"]["body"] == SENTINEL
    assert SENTINEL in rec["payload"]["result"]


# ---------------------------------------------------------------- test 3
def test_survives_restart_fresh_instance(log, log_path):
    log.record(tool="run_shell", tier="confirm", status="ok", gate="approved",
               args={"command": "echo hi"}, result="OK: exit 0")
    fresh = audit.AuditLog(log_path)
    (rec,) = fresh.read()
    assert rec["tool"] == "run_shell"
    assert rec["payload"]["args"]["command"] == "echo hi"


def test_survives_restart_cross_process(log_path):
    """Real restart persistence: subprocess A writes, fresh subprocess B reads
    (mirrors the slice-10 memory-store proof)."""
    write = ("import sys; from jarvis.core.audit import AuditLog; "
             "AuditLog(sys.argv[1]).record(tool='launch_app', tier='auto', "
             "status='ok', args={'name': 'calc'}, result='OK.')")
    read = ("import sys, json; from jarvis.core.audit import AuditLog; "
            "print(json.dumps(AuditLog(sys.argv[1]).read()))")
    root = str(Path(__file__).resolve().parent.parent)
    for code in (write, read):
        r = subprocess.run([sys.executable, "-c", code, str(log_path)],
                           capture_output=True, text=True, cwd=root, timeout=60)
        assert r.returncode == 0, r.stderr
    records = json.loads(r.stdout)
    assert len(records) == 1
    assert records[0]["tool"] == "launch_app"
    assert records[0]["payload"]["args"] == {"name": "calc"}


# ---------------------------------------------------------------- test 10
def test_result_truncation(log):
    big = "R" * 5000
    log.record(tool="read_page", tier="auto", status="ok",
               args={"note": SENTINEL + "-args-stay-whole"}, result=big)
    (rec,) = log.read()
    payload = rec["payload"]
    assert payload["truncated"] is True
    assert payload["result_len"] == 5000
    assert len(payload["result"]) == int(_settings.get("audit.result_max_chars", 2000))
    # Args are the accountability-critical verbatim — never truncated.
    assert payload["args"]["note"] == SENTINEL + "-args-stay-whole"


# ---------------------------------------------------------------- test 11
def test_rotation_preserves_old_file(log, log_path):
    _settings.set("audit.max_file_mb", 0.0001, persist=False)  # ~105 bytes
    try:
        log.record(tool="launch_app", tier="auto", status="ok",
                   args={"name": "one"}, result="OK 1")
        log.record(tool="launch_app", tier="auto", status="ok",
                   args={"name": "two"}, result="OK 2")
    finally:
        _settings.set("audit.max_file_mb", 5, persist=False)
    rotated = [p for p in log_path.parent.glob("audit-*.jsonl")]
    assert rotated, "expected a rotated file to exist"
    # Old record intact in the rotated file; new file holds only the new one.
    old = json.loads(rotated[0].read_text(encoding="utf-8").splitlines()[0])
    assert old["tool"] == "launch_app"
    current = _raw(log_path).splitlines()
    assert len(current) == 1
    # Nothing was deleted: every record still exists somewhere.
    total = len(current) + sum(
        len(p.read_text(encoding="utf-8").splitlines()) for p in rotated)
    assert total == 2


# ---------------------------------------------------------------- test 12
def test_dpapi_unavailable_envelope_only(log, log_path, monkeypatch):
    def boom(_data):
        raise RuntimeError("DPAPI down")
    monkeypatch.setattr(dpapi, "protect", boom)
    monkeypatch.setattr(dpapi, "available", lambda: False)
    assert log.record(tool="send_email", tier="confirm", status="ok",
                      gate="approved", args={"body": SENTINEL},
                      result=SENTINEL)
    raw = _raw(log_path)
    assert SENTINEL not in raw            # never plaintext, even degraded
    assert json.loads(raw)["enc"] is None  # on-disk: no payload blob at all
    (rec,) = log.read()
    assert rec["payload"] is None
    assert "unavailable" in rec["payload_error"]
    assert rec["tool"] == "send_email"    # the timeline survives
