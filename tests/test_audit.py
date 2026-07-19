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


def _records():
    """The per-test isolated singleton the execute()/brain splices write to
    (swapped to tmp_path by the conftest autouse fixture)."""
    return audit.audit_log.read()


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


# ---------------------------------------------------------------- slice 28:
# read_envelopes (no-decrypt timeline) + read_payload (decrypt ONE record) —
# the primitives the audit-viewer endpoints stand on.

def test_read_envelopes_does_not_decrypt(log):
    if not dpapi.available():
        pytest.fail("DPAPI must be available on this machine (pywin32)")
    log.record(tool="send_email", tier="confirm", status="ok", gate="approved",
               args={"body": SENTINEL}, result=SENTINEL)
    log.record(tool="launch_app", tier="auto", status="ok",
               args={"name": "notepad"}, result="OK")
    envs = log.read_envelopes()
    assert [e["index"] for e in envs] == [0, 1]          # file order, indexed
    for e in envs:
        assert e["has_payload"] is True
        assert "args" not in e and "payload" not in e     # NEVER decrypted here
        for k in ("ts", "tool", "tier", "gate", "status", "dry_run"):
            assert k in e
    assert SENTINEL not in json.dumps(envs)               # the privacy contract


def test_read_payload_by_index_roundtrip(log):
    if not dpapi.available():
        pytest.fail("DPAPI must be available on this machine (pywin32)")
    log.record(tool="send_email", tier="confirm", status="ok", gate="approved",
               args={"body": SENTINEL}, result=f"OK {SENTINEL}")
    log.record(tool="launch_app", tier="auto", status="ok",
               args={"name": "notepad"}, result="OK")
    p0 = log.read_payload(0)
    assert p0["args"] == {"body": SENTINEL}
    assert SENTINEL in json.dumps(p0)                     # decrypt only on ask
    assert log.read_payload(1)["args"] == {"name": "notepad"}
    assert log.read_payload(99) is None                   # out of range, honest


def test_read_payload_surfaces_error_when_encrypted_unavailable(log, monkeypatch):
    def boom(_data):
        raise RuntimeError("DPAPI down")
    monkeypatch.setattr(dpapi, "protect", boom)
    monkeypatch.setattr(dpapi, "available", lambda: False)
    log.record(tool="send_email", tier="confirm", status="ok",
               args={"body": SENTINEL}, result=SENTINEL)
    # enc is null on disk -> read_envelopes flags no payload, read_payload errors
    (env,) = log.read_envelopes()
    assert env["has_payload"] is False and "payload_error" in env
    p = log.read_payload(0)
    assert "payload_error" in p and "args" not in p


# ================================================================ Stage 2:
# the execute()/brain splices — gate outcomes, blocked, unknown tool,
# write-failure posture, meta-tool mutations. Patterns mirror test_shell.py.

import threading as _threading
import time as _time

from jarvis import primitives
from jarvis.core import chain as _chain
from jarvis.core.confirmations import confirmations as _confirmations
from jarvis.primitives import files as _files, shell as _shell


def _auto_resolve(approved: bool):
    def responder(event):
        if event.get("type") == "confirm_request":
            _threading.Thread(target=lambda: (
                _time.sleep(0.05),
                _confirmations.resolve(event["id"], approved))).start()
    return _confirmations.subscribe(responder)


@pytest.fixture(autouse=True)
def _broadcaster_back_to_idle():
    """Leak guard: execute() called outside think() deliberately parks the
    broadcaster at THINKING (think()'s finally normally restores IDLE).
    Without this reset, these tests leak THINKING into test_chain's autouse
    IDLE assertion (this file sorts before test_chain; test_shell sorts
    after, which is why the same direct-execute pattern never tripped it)."""
    yield
    from jarvis.state import AgentState, broadcaster
    broadcaster.set(AgentState.IDLE)


@pytest.fixture()
def no_exec(monkeypatch):
    """No shell may EVER spawn in these tests — the spy raises if reached."""
    def boom(*_a, **_k):
        raise AssertionError("a subprocess was spawned — it must not run")
    monkeypatch.setattr("subprocess.Popen", boom)
    monkeypatch.setattr("subprocess.run", boom)


@pytest.fixture()
def tmp_workspace(tmp_path, monkeypatch):
    ws = tmp_path / "agent_files"
    ws.mkdir()
    monkeypatch.setattr(_files, "AGENT_FILES_DIR", ws)
    return ws


# ---------------------------------------------------------------- test 4
def test_declined_action_logged(monkeypatch):
    """THE slice requirement: a denial is a first-class audit record."""
    ran = []
    monkeypatch.setattr(_shell, "run_shell",
                        lambda cmd: ran.append(cmd) or {"ok": True, "message": "x",
                                                        "exit_code": 0, "stdout": "",
                                                        "stderr": ""})
    unsub = _auto_resolve(False)
    try:
        out = primitives.execute("run_shell", {"command": "echo decline-me"})
    finally:
        unsub()
    assert "CANCELLED" in out and ran == []
    (rec,) = _records()
    assert rec["tool"] == "run_shell"
    assert rec["tier"] == "confirm"
    assert rec["gate"] == "declined"
    assert rec["status"] == "cancelled"
    assert rec["payload"]["args"]["command"] == "echo decline-me"


# ---------------------------------------------------------------- test 5
def test_timeout_logged(monkeypatch):
    monkeypatch.setattr(_shell, "run_shell", lambda cmd: pytest.fail("must not run"))
    _settings.set("confirm.timeout_s", 0.3, persist=False)
    try:
        out = primitives.execute("run_shell", {"command": "echo too-slow"})
    finally:
        _settings.set("confirm.timeout_s", 30, persist=False)
    assert "CANCELLED" in out
    (rec,) = _records()
    assert rec["gate"] == "timeout"
    assert rec["status"] == "cancelled"


# ---------------------------------------------------------------- test 6
def test_blocked_logged_and_never_ran(no_exec):
    """THE slice requirement: BLOCKED is recorded; the spy proves nothing ran."""
    out = primitives.execute("run_shell", {"command": "rmdir /s /q C:\\"})
    assert out.startswith("BLOCKED")
    (rec,) = _records()
    assert rec["tier"] == "blocked"
    assert rec["gate"] is None
    assert rec["status"] == "failed"
    assert rec["payload"]["args"]["command"] == "rmdir /s /q C:\\"


# ---------------------------------------------------------------- test 7
def test_approved_success_logged():
    unsub = _auto_resolve(True)
    try:
        out = primitives.execute("run_shell", {"command": "echo audit-approved"})
    finally:
        unsub()
    assert out.startswith("OK"), out
    (rec,) = _records()
    assert rec["gate"] == "approved"
    assert rec["status"] == "ok"
    assert "audit-approved" in rec["payload"]["result"]


# ---------------------------------------------------------------- test 8
def test_auto_tier_logged_gate_null(tmp_workspace):
    (tmp_workspace / "invoice.pdf").write_text("x")
    out = primitives.execute("search_files", {"query": "invoice"})
    assert out.startswith("OK"), out
    (rec,) = _records()
    assert rec["tier"] == "auto"
    assert rec["gate"] is None
    assert rec["status"] == "ok"


# ---------------------------------------------------------------- test 9
def test_unknown_tool_logged():
    out = primitives.execute("no_such_tool_xyz", {"a": 1})
    assert "Unknown tool" in out
    (rec,) = _records()
    assert rec["tool"] == "no_such_tool_xyz"
    assert rec["tier"] == "unknown"
    assert rec["status"] == "failed"


# ---------------------------------------------------------------- test 13
def test_audit_write_failure_does_not_block_action(tmp_workspace, monkeypatch):
    """Loud-but-alive: the action still runs; the gap is appended (never
    prepended — the status prefix must survive for status_from_result)."""
    def boom(**_kw):
        raise OSError("disk full")
    monkeypatch.setattr(audit.audit_log, "record", boom)
    (tmp_workspace / "a.txt").write_text("x")
    out = primitives.execute("search_files", {"query": "a"})
    assert out.startswith("OK"), out
    assert "audit log write failed" in out
    assert _chain.status_from_result(out) == "ok"


# ---------------------------------------------------------------- test 14
def test_remember_forget_audited(tmp_path):
    from jarvis.brain import JarvisBrain
    from jarvis.core.memory import MemoryStore
    brain = JarvisBrain()
    brain.memory = MemoryStore(tmp_path / "mem" / "memories.bin")
    out = brain._memory_tool("remember", {"text": "audit likes tea"})
    assert out.startswith("OK"), out
    out2 = brain._memory_tool("recall", {})
    assert out2.startswith("OK"), out2
    out3 = brain._memory_tool("forget", {"query": "audit likes tea"})
    assert out3.startswith("OK"), out3
    recs = _records()
    assert [r["tool"] for r in recs] == ["remember", "forget"]  # no recall
    assert recs[0]["payload"]["args"]["text"] == "audit likes tea"
    assert all(r["status"] == "ok" for r in recs)


# ---------------------------------------------------------------- test 15
def test_audit_disabled_no_write(tmp_workspace):
    _settings.set("audit.enabled", False, persist=False)
    try:
        (tmp_workspace / "b.txt").write_text("x")
        out = primitives.execute("search_files", {"query": "b"})
    finally:
        _settings.set("audit.enabled", True, persist=False)
    assert out.startswith("OK"), out
    assert "audit log write failed" not in out   # off is not a failure
    assert not audit.audit_log.path.exists()
    assert _records() == []
