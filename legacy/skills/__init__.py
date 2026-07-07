"""Importing this package registers every skill (via @register_skill).

Add a skill = drop a module here and list it below. A module that fails to
import (missing optional dep) is skipped, never fatal — the rest still load.
"""
from __future__ import annotations

import importlib

_SKILL_MODULES = [
    "skills.pc_control",
    "skills.file_manager",
    "skills.system_monitor",
    "skills.automation",
    "skills.web_research",
    "skills.productivity",
    "skills.code_assistant",
    "skills.communication",
    "skills.screen_vision",
    "skills.clipboard",
    "skills.media_control",
    "skills.finance_tracker",
]

for _mod in _SKILL_MODULES:
    try:
        importlib.import_module(_mod)
    except Exception as _exc:  # noqa: BLE001
        print(f"  [skills] skipped {_mod}: {_exc}")
