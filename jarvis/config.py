"""JARVIS low-level configuration: paths, .env loading, secret access.

Day-to-day settings live in data/settings.json (managed by
jarvis.core.settings_store). This module only holds filesystem paths and
secret (API key) plumbing.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Repo root (this file lives in jarvis/, one level down).
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
ENV_FILE = BASE_DIR / ".env"

DATA_DIR.mkdir(exist_ok=True)

load_dotenv(ENV_FILE)

# Map provider name -> env var holding its API key.
KEY_ENV_VARS = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
}

SERVER_HOST = "127.0.0.1"  # localhost ONLY — never expose beyond this machine
SERVER_PORT = 8000


def get_api_key(provider: str) -> str | None:
    """Return the API key for a provider, or None if not configured."""
    var = KEY_ENV_VARS.get(provider)
    if not var:
        return None
    value = os.environ.get(var, "").strip()
    return value or None


def mask_key(value: str | None) -> str:
    """Mask a secret to its last 4 characters for display/logging."""
    if not value:
        return ""
    return ("•" * 8) + value[-4:] if len(value) > 4 else "•" * 8


def set_api_key(provider: str, value: str) -> bool:
    """Persist an API key to .env and the live environment.

    Full secrets live ONLY in .env — never in settings.json, never logged.
    """
    var = KEY_ENV_VARS.get(provider)
    if not var:
        return False
    value = value.strip()
    lines: list[str] = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.split("#", 1)[0].strip().startswith(f"{var}="):
            lines[i] = f"{var}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{var}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[var] = value
    return True
