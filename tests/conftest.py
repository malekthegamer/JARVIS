"""Make the repo root importable so `import jarvis` works from anywhere."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_audit_log(tmp_path, monkeypatch):
    """Slice 18: point the process-wide audit log at a per-test temp file.

    Without this, every full-suite run appends hundreds of test records —
    including live email bodies — to the REAL data/audit/ log. Splices reach
    the singleton via the module attribute (audit.audit_log), so this swap
    always intercepts. This is deliberately the only autouse fixture in
    conftest (a named deviation from the bare-conftest precedent)."""
    from jarvis.core import audit
    monkeypatch.setattr(
        audit, "audit_log", audit.AuditLog(tmp_path / "audit" / "audit.jsonl"))
