"""JARVIS web dashboard — FastAPI + WebSocket, strictly localhost.

    python server.py            ->  http://localhost:8000

Thin wrapper over the SAME core objects the terminal uses (JarvisBrain,
voice_manager, settings, audit log). No logic lives here — only transport.
"""
from __future__ import annotations

import asyncio
import base64
import threading
import uuid

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import config
from brain import jarvis_brain
from core import audit_log, confirmations, memory, notifications, skill_registry
from core.settings_store import settings
from providers import registry

app = FastAPI(title="JARVIS", docs_url=None, redoc_url=None)

_clients: set[WebSocket] = set()
_loop: asyncio.AbstractEventLoop | None = None
_pending_confirms: dict[str, dict] = {}  # id -> {"event": Event, "approved": bool}


# ---------------------------------------------------------------- broadcast --
async def _broadcast(payload: dict) -> None:
    dead = []
    for ws in list(_clients):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


def broadcast_threadsafe(payload: dict) -> None:
    """Push an event to every dashboard tab from any thread."""
    if _loop is not None:
        asyncio.run_coroutine_threadsafe(_broadcast(payload), _loop)


# ------------------------------------------------- dashboard confirm modal --
def _dashboard_confirm(description: str) -> bool:
    """confirmations frontend: modal in the dashboard, 120 s timeout -> deny."""
    if not _clients:
        return False  # nobody to ask -> fail closed
    confirm_id = uuid.uuid4().hex
    entry = {"event": threading.Event(), "approved": False}
    _pending_confirms[confirm_id] = entry
    broadcast_threadsafe({"type": "confirm_request", "id": confirm_id, "description": description})
    entry["event"].wait(timeout=120)
    _pending_confirms.pop(confirm_id, None)
    return entry["approved"]


# ------------------------------------------------------------------ startup --
@app.on_event("startup")
async def _startup() -> None:
    global _loop
    _loop = asyncio.get_running_loop()
    skill_registry.load_all()
    confirmations.set_frontend(_dashboard_confirm)
    jarvis_brain.on_status = lambda state: broadcast_threadsafe({"type": "status", "state": state})
    audit_log.subscribe(lambda entry: broadcast_threadsafe({"type": "audit", "entry": entry}))
    notifications.subscribe(lambda item: broadcast_threadsafe({"type": "notification", "item": item}))
    try:
        from core import scheduler
        scheduler.start()
    except Exception:
        pass


# ---------------------------------------------------------------- websocket --
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _clients.add(ws)
    try:
        await ws.send_json({"type": "status", "state": "idle"})
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "chat":
                text = str(msg.get("text", "")).strip()
                if not text:
                    continue
                speak = bool(msg.get("speak", False))
                await _broadcast({"type": "user", "text": text})
                asyncio.get_running_loop().run_in_executor(None, _handle_chat, text, speak)
            elif kind == "confirm_response":
                entry = _pending_confirms.get(msg.get("id", ""))
                if entry:
                    entry["approved"] = bool(msg.get("approved"))
                    entry["event"].set()
            elif kind == "reset":
                jarvis_brain.reset()
                await _broadcast({"type": "reset"})
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)


def _handle_chat(text: str, speak: bool) -> None:
    """Runs on a worker thread — think(), broadcast, optionally speak on the PC."""
    reply = jarvis_brain.think(text)
    broadcast_threadsafe({"type": "assistant", "text": reply})
    if speak:
        try:
            from voice.voice_manager import voice_manager
            broadcast_threadsafe({"type": "status", "state": "speaking"})
            voice_manager.speak(reply)
        except Exception:
            pass
        finally:
            broadcast_threadsafe({"type": "status", "state": "idle"})


# --------------------------------------------------------------- settings API --
@app.get("/api/settings")
def get_settings() -> JSONResponse:
    data = settings.all()
    masked_keys = {name: config.mask_key(config.get_api_key(name))
                   for name in ("gemini", "openai", "claude", "openrouter", "elevenlabs")}
    availability = {family: registry.available(family) for family in ("brain", "tts", "stt")}
    ollama_models: list[str] = []
    try:
        from providers.brain.ollama_provider import OllamaProvider
        ollama_models = OllamaProvider.list_models()
    except Exception:
        pass
    return JSONResponse({
        "settings": data,
        "keys": masked_keys,
        "availability": availability,
        "ollama_models": ollama_models,
        "provider_names": {f: registry.names(f) for f in ("brain", "tts", "stt")},
    })


@app.post("/api/settings")
async def post_settings(payload: dict) -> JSONResponse:
    keys: dict = payload.get("keys") or {}
    for provider, value in keys.items():
        if value and "•" not in value:  # ignore masked placeholders echoed back
            config.set_api_key(provider, value)
            audit_log.log_action("settings", "api_key_saved", {"provider": provider})
    new_settings: dict = payload.get("settings") or {}
    if new_settings:
        settings.save(new_settings)  # persists + hot-reloads providers
        audit_log.log_action("settings", "saved", {"sections": list(new_settings.keys())})
    else:
        settings.reload()
    return get_settings()


@app.get("/api/voices")
def get_voices(provider: str = "edge_tts") -> JSONResponse:
    voices: list[dict] = []
    try:
        if provider == "edge_tts":
            from providers.tts.edge_tts_provider import EdgeTTSProvider
            voices = EdgeTTSProvider.list_voices()
        elif provider == "elevenlabs":
            p = registry.get("tts", "elevenlabs")
            if p and p.is_configured():
                voices = p.list_voices()
    except Exception:
        pass
    return JSONResponse({"voices": voices})


@app.get("/api/mics")
def get_mics() -> JSONResponse:
    try:
        from voice.capture import find_real_mic, is_probably_real_mic, list_input_devices
        devices = [{"index": i, "name": n, "real": is_probably_real_mic(n)}
                   for i, n in list_input_devices()]
        auto_index, auto_name = find_real_mic()
        return JSONResponse({"devices": devices, "auto": {"index": auto_index, "name": auto_name}})
    except Exception as exc:
        return JSONResponse({"devices": [], "auto": None, "error": str(exc)})


@app.post("/api/tts_test")
async def tts_test(payload: dict) -> JSONResponse:
    text = str(payload.get("text") or "Good evening, sir. All systems operational.")

    def _speak():
        try:
            from voice.voice_manager import voice_manager
            voice_manager.speak(text)
        except Exception:
            pass

    asyncio.get_running_loop().run_in_executor(None, _speak)
    return JSONResponse({"ok": True})


# ------------------------------------------------------------------ data API --
@app.get("/api/status")
def get_status() -> JSONResponse:
    from voice.voice_manager import voice_manager
    brain_name = settings.get("brain.active")
    provider = registry.get("brain", brain_name)
    return JSONResponse({
        "brain": {"name": brain_name, "configured": bool(provider and provider.is_configured()),
                   "model": settings.get(f"brain.models.{brain_name}", "")},
        "stt": voice_manager.resolve_stt_name(),
        "tts": voice_manager.resolve_tts_name(),
    })


@app.get("/api/audit")
def get_audit(n: int = 50) -> JSONResponse:
    return JSONResponse({"entries": audit_log.recent(n)})


@app.get("/api/notifications")
def get_notifications() -> JSONResponse:
    return JSONResponse({"items": notifications.all_notifications()})


@app.post("/api/notifications/read")
def mark_notifications_read() -> JSONResponse:
    notifications.mark_read()
    return JSONResponse({"ok": True})


@app.get("/api/history")
def get_history() -> JSONResponse:
    msgs = [{"role": m["role"], "text": m.get("content", "")}
            for m in jarvis_brain.history if m["role"] in ("user", "assistant") and m.get("content")]
    return JSONResponse({"messages": msgs})


@app.get("/api/memories")
def get_memories() -> JSONResponse:
    return JSONResponse({"memories": memory.list_memories()})


# -------------------------------------------------------------------- pages --
@app.get("/")
def index() -> FileResponse:
    return FileResponse(config.BASE_DIR / "static" / "index.html")


@app.get("/settings")
def settings_page() -> FileResponse:
    return FileResponse(config.BASE_DIR / "static" / "settings.html")


app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "static")), name="static")


if __name__ == "__main__":
    # localhost ONLY — never expose beyond this machine (spec Section 11).
    uvicorn.run(app, host=config.SERVER_HOST, port=config.SERVER_PORT, log_level="warning")
