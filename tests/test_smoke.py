"""Stage-1 exit tests: the package imports and every skeleton provider
registered. Catches import rot from the legacy -> jarvis namespace move
(registry.load_all swallows import errors, so a broken provider simply
goes missing from names() — which these assertions catch)."""
from __future__ import annotations


def test_package_imports():
    import jarvis  # noqa: F401
    from jarvis import config, state  # noqa: F401
    from jarvis.core import errors, settings_store  # noqa: F401
    from jarvis.voice import capture, playback, voice_manager  # noqa: F401


def test_registry_has_skeleton_providers():
    from jarvis.providers import registry
    assert "gemini" in registry.names("brain")
    assert "google" in registry.names("stt")
    assert set(registry.names("tts")) >= {"edge_tts", "pyttsx3"}


def test_provider_instances_construct():
    from jarvis.providers import registry
    for family, name in [("brain", "gemini"), ("stt", "google"),
                         ("tts", "edge_tts"), ("tts", "pyttsx3")]:
        provider = registry.get(family, name)
        assert provider is not None, f"{family}/{name} failed to construct"


def test_settings_defaults():
    from jarvis.core.settings_store import settings
    assert settings.get("brain.active") == "gemini"
    assert settings.get("stt.active") == "google"
    assert settings.get("brain.models.gemini")  # non-empty model id


def test_gemini_key_configured():
    """The skeleton's definition of done needs a live brain — fail loudly
    here rather than mysteriously in stage 2."""
    from jarvis import config
    assert config.get_api_key("gemini"), "GEMINI_API_KEY missing from .env"
