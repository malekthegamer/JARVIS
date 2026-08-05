"""Slice 62 stage 3 — the measuring tool must not contaminate the measurement.

harness_reliability.py reads the real audit log to report how often each tool
fails. Harnesses are scripts, not pytest, so conftest's per-test audit
isolation never applies to them and their runs land in that same real log.
Three of the seven `click` failures I reported as real-use evidence were in
fact my own tests/harness_visionpad.py.

These tests pin the fix and, more importantly, catch the NEXT harness someone
writes without the guard.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

# Harnesses with a deliberate reason not to redirect — see tests/_harness_env.py.
EXEMPT = {
    "harness_audit_visual.py",   # sets JARVIS_AUDIT_FILE itself, for a seeded log
    "harness_reliability.py",    # must READ the owner's real log
}


def _harnesses() -> list[Path]:
    return sorted(TESTS.glob("harness_*.py"))


def test_there_are_harnesses_to_check():
    """Guard the guard: a glob that silently matches nothing would make every
    other test here vacuously green."""
    assert len(_harnesses()) >= 20, [p.name for p in _harnesses()]


@pytest.mark.parametrize("path", _harnesses(), ids=lambda p: p.name)
def test_every_harness_that_imports_jarvis_isolates_its_audit_log(path: Path):
    """Any harness reaching into jarvis can execute a primitive, and any
    primitive execution writes an audit entry."""
    src = path.read_text(encoding="utf-8")
    if "from jarvis" not in src and "import jarvis" not in src:
        return  # a pure UI surface (e.g. harness_visionpad's Tk window)
    if path.name in EXEMPT:
        return
    imported = any("_harness_env" in ln and ln.strip().startswith(("import ", "from "))
                   for ln in src.splitlines())
    assert imported, (
        f"{path.name} imports jarvis but not tests/_harness_env — its runs will "
        f"be written into the owner's real audit log and will show up in "
        f"harness_reliability.py as if they were real use.")


@pytest.mark.parametrize("path", _harnesses(), ids=lambda p: p.name)
def test_the_guard_is_imported_before_jarvis(path: Path):
    """jarvis.core.audit reads JARVIS_AUDIT_FILE once, at import. A guard that
    lands after the first `from jarvis` line does nothing at all."""
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    # Only a real import line counts. Prose mentioning the module (the caveat
    # harness_reliability prints) must not be mistaken for the guard.
    guard = next((i for i, ln in enumerate(lines) if "_harness_env" in ln
                  and ln.strip().startswith(("import ", "from "))), None)
    if guard is None:
        return
    jarvis_lines = [i for i, ln in enumerate(lines)
                    if ln.strip().startswith(("from jarvis", "import jarvis"))]
    if jarvis_lines:
        assert guard < min(jarvis_lines), (
            f"{path.name}: _harness_env imported at line {guard + 1}, after the "
            f"first jarvis import at line {min(jarvis_lines) + 1} — too late.")


def test_the_guard_actually_redirects_the_audit_log(tmp_path):
    """The behavioural check, in a real subprocess: with the guard imported,
    jarvis.core.audit must not be pointed at data/audit/."""
    code = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "from tests import _harness_env\n"
        "from jarvis.core import audit\n"
        "print(audit.audit_log.path)\n" % str(ROOT)
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr
    used = Path(out.stdout.strip())
    real = (ROOT / "data" / "audit").resolve()
    assert real not in used.resolve().parents, (
        f"guard did not redirect: audit log is {used}")
    assert "jarvis-harness-audit" in str(used), used


def test_an_explicitly_set_audit_file_is_honoured(tmp_path, monkeypatch):
    """harness_audit_visual seeds a specific file for a server process to read;
    the guard must never overwrite a choice someone made on purpose."""
    target = tmp_path / "seeded.jsonl"
    code = (
        "import os, sys\n"
        "os.environ['JARVIS_AUDIT_FILE'] = r'%s'\n"
        "sys.path.insert(0, r'%s')\n"
        "from tests import _harness_env\n"
        "print(_harness_env.HARNESS_AUDIT_FILE)\n" % (str(target), str(ROOT))
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == str(target), out.stdout
