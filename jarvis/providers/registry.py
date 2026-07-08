"""Self-registering provider registry for the three provider families.

Adding a provider = drop one file under jarvis/providers/<family>/ containing
a @register("family", "name") class and list it in _PROVIDER_MODULES. Nothing
else in the codebase changes.

Skeleton slice ships only the four providers the walking skeleton needs;
the rest (Claude/OpenAI/Ollama/Whisper/ElevenLabs) return from legacy/ in
their own slices.
"""
from __future__ import annotations

import importlib
from typing import Any

_classes: dict[str, dict[str, type]] = {"brain": {}, "tts": {}, "stt": {}}
_instances: dict[tuple[str, str], Any] = {}

# Modules imported for side-effect registration — the single seam.
_PROVIDER_MODULES = [
    "jarvis.providers.brain.gemini_provider",
    "jarvis.providers.tts.edge_tts_provider",
    "jarvis.providers.tts.pyttsx3_provider",
    "jarvis.providers.stt.google_stt_provider",
]

_loaded = False


def register(family: str, name: str):
    def decorator(cls):
        cls.name = name
        _classes[family][name] = cls
        return cls
    return decorator


def load_all() -> None:
    """Import every provider module so registration decorators run.
    A provider whose import fails (missing optional dep) is skipped, not fatal."""
    global _loaded
    if _loaded:
        return
    for module in _PROVIDER_MODULES:
        try:
            importlib.import_module(module)
        except Exception:
            pass
    _loaded = True


def get(family: str, name: str):
    """Return the (cached) provider instance, or None if unknown/unimportable."""
    load_all()
    key = (family, name)
    if key not in _instances:
        cls = _classes.get(family, {}).get(name)
        if cls is None:
            return None
        try:
            _instances[key] = cls()
        except Exception:
            return None
    return _instances[key]


def names(family: str) -> list[str]:
    load_all()
    return list(_classes.get(family, {}).keys())


def available(family: str) -> dict[str, bool]:
    """name -> is_configured() map, for the settings page."""
    load_all()
    out = {}
    for name in names(family):
        provider = get(family, name)
        try:
            out[name] = bool(provider and provider.is_configured())
        except Exception:
            out[name] = False
    return out
