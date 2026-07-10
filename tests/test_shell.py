"""Slice-9 run_shell tests. STAGE 1 = the safety core: the denylist and the
BLOCKED tier. Every denylist case drives the FULL executor with subprocess
monkeypatched to a spy that RAISES if called — so a denylisted command
provably cannot execute even if the classification logic were wrong.
"""
from __future__ import annotations

import subprocess

import pytest

from jarvis import primitives
from jarvis.core import chain
from jarvis.primitives import shell


@pytest.fixture()
def no_exec(monkeypatch):
    """Spawning ANY process during a denylist test is a hard failure."""
    def boom(*a, **k):
        raise AssertionError(f"subprocess must NOT run for a denylisted command: {a}")
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)


# Commands that MUST be refused outright (blocked before any modal / exec).
DENYLISTED = [
    "rm -rf /",
    "rm -rf /*",
    "rm -fr ~/",
    "sudo rm -rf /etc",
    "rm -r -f /usr/bin",
    "del /s /q C:\\",
    "rd /s /q C:\\Windows",
    "rmdir /s C:\\Users\\malek",
    "Remove-Item -Recurse -Force C:\\",
    "format C:",
    "format /q D:",
    "diskpart clean all",
    "Clear-Disk -Number 0 -RemoveData",
    "Format-Volume -DriveLetter D",
    "cipher /w:C",
    "dd if=/dev/zero of=/dev/sda",
    ":(){ :|:& };:",
    "%0|%0",
]

# Commands that are risky but NOT definitively catastrophic — these MUST stay
# CONFIRM-only (the denylist is narrow; CONFIRM is the control for the tail).
SAFE_TO_CONFIRM = [
    "echo hello",
    "whoami",
    "git status",
    "del myfile.txt",
    "rm -rf ./build",
    "rm -rf node_modules",
    "shutdown /s /t 0",
    "Format-List",                       # a PowerShell cmdlet, not a disk format
    "ls -la /home/me/project",
    "python manage.py migrate",
]


@pytest.mark.parametrize("cmd", DENYLISTED)
def test_denylisted_command_blocked_never_runs(cmd, no_exec):
    out = primitives.execute("run_shell", {"command": cmd})
    assert out.startswith("BLOCKED"), out
    # the no_exec spy guarantees no process was spawned


@pytest.mark.parametrize("cmd", SAFE_TO_CONFIRM)
def test_safe_destructive_stays_confirm_not_blocked(cmd):
    info = shell.classify_run_shell({"command": cmd})
    assert info["tier"] == "confirm", (cmd, info)


def test_obfuscated_command_not_caught_reaches_confirm():
    """HONESTY: the denylist is a backstop, not a boundary. A base64-obfuscated
    catastrophic command is NOT caught — it reaches CONFIRM (the human is the
    control). This test exists so we never over-claim the denylist."""
    import base64
    payload = base64.b64encode(b"rm -rf /").decode()
    obf = f'echo {payload} | base64 -d | sh'
    info = shell.classify_run_shell({"command": obf})
    assert info["tier"] == "confirm", info


def test_classify_confirm_carries_verbatim_command_and_shell():
    info = shell.classify_run_shell({"command": "echo hi"})
    assert info["tier"] == "confirm"
    assert info["command"] == "echo hi"          # verbatim, for the modal box
    assert "cmd.exe" in info["description"].lower()


def test_blocked_result_recorded_as_failed_in_chain():
    """A BLOCKED result must render as a failed (amber) Action Log row, never
    a green check."""
    assert chain.status_from_result("BLOCKED (root_recursive_delete): nope") == "failed"
    assert chain.status_from_result("OK: exit 0") == "ok"


def test_empty_command_refused():
    info = shell.classify_run_shell({"command": "   "})
    assert info["tier"] == "blocked"
    out = primitives.execute("run_shell", {"command": ""})
    assert out.startswith("BLOCKED")


def test_run_shell_is_registered_confirm_tier():
    assert "run_shell" in primitives.PRIMITIVES
    assert "classify" in primitives.PRIMITIVES["run_shell"]


# ---------- STAGE 2: execution engine (honest output + timeout tree-kill) ----------
# Harmless commands only. cmd.exe semantics (this project is Windows).

import time


def test_echo_ok_captures_stdout():
    r = shell.run_shell("echo hello-from-shell")
    assert r["ok"] is True
    assert r["exit_code"] == 0
    assert "hello-from-shell" in r["stdout"]
    assert "exit 0" in r["message"] and "hello-from-shell" in r["message"]


def test_nonzero_exit_is_failed_with_code():
    r = shell.run_shell("cmd /c exit 3")
    assert r["ok"] is False
    assert r["exit_code"] == 3
    assert "3" in r["message"]


def test_partial_success_stdout_plus_nonzero_is_failed():
    """A command that prints THEN fails must never read as OK, but its output
    is still reported."""
    r = shell.run_shell("echo partial-output & cmd /c exit 7")
    assert r["ok"] is False, r
    assert r["exit_code"] == 7
    assert "partial-output" in r["stdout"]
    assert "partial-output" in r["message"]


def test_stderr_captured():
    # `dir` on a nonexistent path writes to stderr and exits nonzero
    r = shell.run_shell("dir C:\\no-such-path-zzz-9000")
    assert r["ok"] is False
    assert r["stderr"].strip() != ""
    assert "stderr" in r["message"].lower()


def test_timeout_kills_tree_and_reports():
    """A command that outlives the timeout is killed (tree) and reported as a
    timeout — it must NOT hang, and no child may linger."""
    settings_before = None
    from jarvis.core.settings_store import settings as _s
    _s.set("shell.timeout_s", 1.5, persist=False)
    try:
        t0 = time.time()
        # ping sleeps ~10s; must be cut off near 1.5s
        r = shell.run_shell("ping -n 10 127.0.0.1")
        elapsed = time.time() - t0
    finally:
        _s.set("shell.timeout_s", 30, persist=False)
    assert r["ok"] is False
    assert "tim" in r["message"].lower()  # "timed out"
    assert elapsed < 6, f"did not return promptly ({elapsed:.1f}s) — tree not killed?"
    # the CHILD (ping) must be gone too — a bare kill would leave it running
    import psutil
    time.sleep(0.5)
    pings = [p.pid for p in psutil.process_iter(["name"])
             if (p.info["name"] or "").lower() == "ping.exe"]
    assert not pings, f"ping child survived the tree kill: {pings}"


def test_bad_command_never_raises():
    r = shell.run_shell("this-is-not-a-real-command-zzz9000")
    assert r["ok"] is False
    assert isinstance(r["message"], str)


def test_output_truncated():
    # emit a lot of lines; result must be capped, not unbounded
    r = shell.run_shell('cmd /c "for /L %i in (1,1,5000) do @echo line-%i"')
    assert len(r["message"]) < 6000
    assert "trunc" in r["message"].lower() or len(r["stdout"]) <= 4100


# ---------- STAGE 3: gate integration + kill switch ----------

import threading as _threading
import time as _time2

from jarvis.core.confirmations import confirmations as _confirmations
from jarvis.core.settings_store import settings as _settings


def _auto_resolve(approved: bool):
    def responder(event):
        if event.get("type") == "confirm_request":
            _threading.Thread(target=lambda: (
                _time2.sleep(0.05),
                _confirmations.resolve(event["id"], approved))).start()
    return _confirmations.subscribe(responder)


def test_gate_walk_approve_runs():
    """Approve -> the command actually runs (harmless echo)."""
    unsub = _auto_resolve(True)
    try:
        out = primitives.execute("run_shell", {"command": "echo gated-approved-ok"})
    finally:
        unsub()
    assert out.startswith("OK"), out
    assert "gated-approved-ok" in out


def test_gate_decline_does_not_run(monkeypatch):
    """Decline -> the command never executes (spy proves it)."""
    ran = []
    monkeypatch.setattr(shell, "run_shell",
                        lambda cmd: ran.append(cmd) or {"ok": True, "message": "x",
                                                        "exit_code": 0, "stdout": "",
                                                        "stderr": ""})
    unsub = _auto_resolve(False)
    try:
        out = primitives.execute("run_shell", {"command": "echo should-not-run"})
    finally:
        unsub()
    assert "CANCELLED" in out
    assert ran == [], "declined command must NOT execute"


def test_gate_timeout_does_not_run(monkeypatch):
    ran = []
    monkeypatch.setattr(shell, "run_shell",
                        lambda cmd: ran.append(cmd) or {"ok": True, "message": "x",
                                                        "exit_code": 0, "stdout": "",
                                                        "stderr": ""})
    _settings.set("confirm.timeout_s", 0.3, persist=False)
    try:
        out = primitives.execute("run_shell", {"command": "echo nope"})
    finally:
        _settings.set("confirm.timeout_s", 30, persist=False)
    assert "CANCELLED" in out
    assert ran == []


def test_gate_carries_command_to_modal(monkeypatch):
    """The gate must pass the VERBATIM command to the confirm event."""
    seen = {}
    unsub = _confirmations.subscribe(
        lambda e: seen.update(cmd=e.get("command")) if e.get("type") == "confirm_request" else None)
    approver = _auto_resolve(True)
    try:
        primitives.execute("run_shell", {"command": "echo verbatim-check"})
    finally:
        approver()
        unsub()
    assert seen.get("cmd") == "echo verbatim-check"


def test_disabled_hidden_from_schema_and_refused():
    from jarvis.brain import JarvisBrain
    _settings.set("shell.enabled", False, persist=False)
    try:
        names = [t["name"] for t in JarvisBrain().tools()]
        assert "run_shell" not in names, "disabled run_shell must not be advertised"
        out = primitives.execute("run_shell", {"command": "echo hi"})
        assert out.startswith("BLOCKED") and "disabled" in out.lower()
    finally:
        _settings.set("shell.enabled", True, persist=False)
    # re-enabled: advertised again
    assert "run_shell" in [t["name"] for t in JarvisBrain().tools()]
