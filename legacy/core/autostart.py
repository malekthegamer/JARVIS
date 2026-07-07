"""Windows 'launch on startup' registration via the HKCU Run registry key.

Points at `pythonw tray.py` so JARVIS starts minimized to the tray on login.
Wired to the settings 'autostart' toggle (Save & Apply calls sync_from_settings).
"""
from __future__ import annotations

import sys
from pathlib import Path

import config

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "JARVIS"


def _command() -> str:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw if pythonw.exists() else sys.executable)
    return f'"{exe}" "{config.BASE_DIR / "tray.py"}"'


def enable() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _command())
        return True
    except Exception:
        return False


def disable() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _VALUE_NAME)
        return True
    except FileNotFoundError:
        return True  # already absent
    except Exception:
        return False


def is_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
        return True
    except Exception:
        return False


def sync_from_settings() -> None:
    """Make the registry match the settings 'autostart' flag."""
    from core.settings_store import settings
    want = bool(settings.get("autostart", False))
    if want and not is_enabled():
        enable()
    elif not want and is_enabled():
        disable()
