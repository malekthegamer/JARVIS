"""Import this FIRST in any tests/harness_*.py — before importing jarvis.

Harnesses are plain scripts, not pytest, so they never get conftest's per-test
audit isolation: every primitive they execute lands in the OWNER'S REAL
data/audit/ log. Slice 62 measured what that costs. Three of the seven `click`
failures in the reliability report came from tests/harness_visionpad.py runs —
and I then cited that report as evidence about how JARVIS behaves in real use.
The measuring tool was contaminating the measurement.

Importing this module points jarvis.core.audit at a throwaway file instead, so
the reliability report reflects the owner's use and nothing else.

Import ORDER matters: jarvis.core.audit reads JARVIS_AUDIT_FILE exactly once,
at module import, so this has to run before any `from jarvis...` line.

Two harnesses deliberately do NOT import this:
  - harness_audit_visual.py sets JARVIS_AUDIT_FILE itself, to a seeded file a
    separate server process then reads. (An already-set value is honoured here
    anyway, so importing it would be harmless — it just has no reason to.)
  - harness_reliability.py READS the real log; redirecting it would defeat it.
"""
from __future__ import annotations

import os
import tempfile

# Honour an explicit choice; only supply a default when nobody set one.
if not os.environ.get("JARVIS_AUDIT_FILE"):
    _dir = os.path.join(tempfile.gettempdir(), "jarvis-harness-audit")
    os.makedirs(_dir, exist_ok=True)
    os.environ["JARVIS_AUDIT_FILE"] = os.path.join(_dir, f"harness-{os.getpid()}.jsonl")

HARNESS_AUDIT_FILE = os.environ["JARVIS_AUDIT_FILE"]
