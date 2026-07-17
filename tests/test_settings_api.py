"""Slice 23 S2 — the settings HTTP API (salvaged surface, new app).

Isolation is load-bearing: these tests would otherwise rewrite the user's
real .env and data/settings.json. Every test that mutates points config's
ENV_FILE/SETTINGS_FILE at tmp and works on a scratch store; a guard test
proves the real files were never touched.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from jarvis import config, server
from jarvis.core.settings_store import settings


@pytest.fixture()
def client():
    return TestClient(server.app)


@pytest.fixture()
def tmp_env(tmp_path, monkeypatch):
    """Redirect key + settings writes to tmp; restore the live store after."""
    env = tmp_path / ".env"
    sf = tmp_path / "settings.json"
    # settings_store reads config.SETTINGS_FILE at call time, so this alone
    # isolates every read/write; config.set_api_key uses config.ENV_FILE.
    monkeypatch.setattr(config, "ENV_FILE", env)
    monkeypatch.setattr(config, "SETTINGS_FILE", sf)
    yield {"env": env, "settings": sf}
    settings.reload()  # drop any scratch state, reload the real file


# --------------------------------------------------------------- GET shape

def test_get_settings_shape_and_masking(client, monkeypatch):
    monkeypatch.setattr(config, "get_api_key",
                        lambda name: "sk-supersecret-1234" if name == "gemini" else None)
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    for key in ("settings", "keys", "availability", "provider_names"):
        assert key in body, f"missing {key}"
    # the raw secret must NEVER appear; only a mask
    assert "supersecret" not in json.dumps(body)
    assert body["keys"]["gemini"].endswith("1234")
    assert "•" in body["keys"]["gemini"]
    assert "elevenlabs" in body["provider_names"]["tts"]
    assert "local_whisper" in body["provider_names"]["stt"]


# --------------------------------------------------------------- POST save

def test_post_saves_setting_and_hot_reloads(client, tmp_env):
    r = client.post("/api/settings",
                    json={"settings": {"tts": {"edge_voice": "en-US-AriaNeural"}}})
    assert r.status_code == 200
    assert settings.get("tts.edge_voice") == "en-US-AriaNeural"
    # echoed back through a fresh GET
    assert r.json()["settings"]["tts"]["edge_voice"] == "en-US-AriaNeural"


def test_post_key_written_to_env_and_masked_echo_skipped(client, tmp_env, monkeypatch):
    saved = {}
    monkeypatch.setattr(config, "set_api_key",
                        lambda p, v: saved.__setitem__(p, v) or True)
    client.post("/api/settings", json={"keys": {"elevenlabs": "sk-real-key-9999"}})
    assert saved == {"elevenlabs": "sk-real-key-9999"}
    # a masked placeholder echoed back must NOT be re-saved as the key
    saved.clear()
    client.post("/api/settings", json={"keys": {"elevenlabs": "••••••••9999"}})
    assert saved == {}, "masked echo must be ignored, never written"


def test_post_save_is_audited(client, tmp_env, monkeypatch):
    from jarvis.core import audit
    recs = []
    monkeypatch.setattr(audit.audit_log, "record",
                        lambda **kw: recs.append(kw) or True)
    client.post("/api/settings", json={"settings": {"vision": {"enabled": False}}})
    assert any(r.get("tool") == "settings" for r in recs), recs
    # section NAMES only — never the values (a key could sit in the payload)
    blob = json.dumps(recs)
    assert "vision" in blob


def test_wake_toggle_starts_and_stops_listener(client, tmp_env, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "start_wake", lambda: calls.append("start"))
    monkeypatch.setattr(server, "stop_wake", lambda: calls.append("stop"))
    settings.set("wake.enabled", False, persist=False)  # known baseline (this
    # machine may have wake ON live — the toggle only fires on a CHANGE)
    client.post("/api/settings", json={"settings": {"wake": {"enabled": True}}})
    assert "start" in calls
    client.post("/api/settings", json={"settings": {"wake": {"enabled": False}}})
    assert "stop" in calls


def test_autostart_synced_on_save(client, tmp_env, monkeypatch):
    from jarvis.core import autostart
    synced = []
    monkeypatch.setattr(autostart, "sync_from_settings",
                        lambda: synced.append(True))
    client.post("/api/settings", json={"settings": {"autostart": True}})
    assert synced == [True]


# --------------------------------------------------------------- helpers

def test_voices_edge_static_list(client):
    r = client.get("/api/voices?provider=edge_tts")
    assert r.status_code == 200
    voices = r.json()["voices"]
    assert voices and all("id" in v and "label" in v for v in voices)


def test_mics_lists_devices(client):
    r = client.get("/api/mics")
    assert r.status_code == 200
    body = r.json()
    assert "devices" in body and "auto" in body


def test_tts_test_endpoint_ok(client, monkeypatch):
    spoken = []
    monkeypatch.setattr(server.voice_manager, "speak",
                        lambda t: spoken.append(t))
    r = client.post("/api/tts_test", json={})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_settings_route_wired(client):
    """The /settings route exists; serves the page once S3 builds it, else a
    clean 404 (never a 500 / missing route)."""
    from jarvis import config
    r = client.get("/settings")
    if (config.BASE_DIR / "jarvis" / "static" / "settings.html").exists():
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    else:
        assert r.status_code == 404


# --------------------------------------------------------------- isolation

def test_real_files_untouched(client, tmp_path, monkeypatch):
    """A POST under tmp redirection must not write the real .env/settings."""
    real_env = config.BASE_DIR / ".env"
    real_settings = config.SETTINGS_FILE
    before = (real_env.stat().st_mtime if real_env.exists() else None,
              real_settings.stat().st_mtime if real_settings.exists() else None)
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    monkeypatch.setattr(config, "set_api_key", lambda p, v: True)
    client.post("/api/settings", json={"settings": {"telemetry": {"enabled": True}}})
    settings.reload()
    after = (real_env.stat().st_mtime if real_env.exists() else None,
             real_settings.stat().st_mtime if real_settings.exists() else None)
    assert before == after, "the real .env / settings.json must not be written"


def test_every_ui_path_exists_in_defaults():
    """Guard against page/schema drift: every settings path the UI writes must
    exist in DEFAULT_SETTINGS."""
    from jarvis.core.settings_store import DEFAULT_SETTINGS
    ui_paths = [
        "brain.active", "tts.active", "tts.edge_voice", "tts.elevenlabs_voice_id",
        "stt.active", "stt.whisper_model", "stt.whisper_device",
        "stt.mic_device_index", "wake.enabled", "wake.threshold", "autostart",
        "shell.enabled", "email.enabled", "web.enabled", "search.enabled",
        "memory.enabled", "audit.enabled", "vision.enabled", "confirm.timeout_s",
        "input.smooth_cursor", "input.cursor_move_max_ms", "telemetry.enabled",
    ]
    for path in ui_paths:
        node = DEFAULT_SETTINGS
        for part in path.split("."):
            assert part in node, f"UI writes {path} but {part} missing from defaults"
            node = node[part]
