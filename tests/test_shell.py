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
