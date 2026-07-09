"""Thin FastAPI transport over the jarvis core — no business logic here.

- WS /ws:        state + transcript events out; {"type":"chat","text":...} in
- POST /api/listen: push-to-talk — capture one utterance, respond, speak
- GET  /api/state:  current agent state (for reconnect sync / tests)
- /:             serves the HUD from jarvis/static/

Lessons carried from legacy/server.py:
- All blocking voice/brain work runs in the threadpool, never on the loop.
- Events reach browsers through ONE asyncio queue with one consumer task,
  so WS delivery order always matches broadcaster seq order.
- No-store headers on everything: a stale cached hud.js cost a debugging
  session once (legacy commit eef442b).
"""
from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from jarvis.brain import jarvis_brain
from jarvis.core import chain
from jarvis.core.confirmations import confirmations
from jarvis.state import broadcaster
from jarvis.voice.voice_manager import voice_manager

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_CHAT_CHARS = 4000

_clients: set[WebSocket] = set()
_events: asyncio.Queue | None = None
_loop: asyncio.AbstractEventLoop | None = None
_busy = threading.Lock()  # one interaction at a time (re-entrancy guard)
_chat_tasks: set[asyncio.Task] = set()  # keep fire-and-forget tasks alive


def _enqueue_threadsafe(event: dict) -> None:
    """Called from any thread (broadcaster subscribers run under its lock —
    this must stay non-blocking)."""
    loop, queue = _loop, _events
    if loop is None or queue is None or loop.is_closed():
        return
    loop.call_soon_threadsafe(queue.put_nowait, event)


async def _fanout_forever() -> None:
    """Single consumer: preserves event order across all clients."""
    assert _events is not None
    while True:
        event = await _events.get()
        for ws in list(_clients):
            try:
                await ws.send_json(event)
            except Exception:
                _clients.discard(ws)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _loop, _events
    _loop = asyncio.get_running_loop()
    _events = asyncio.Queue()
    unsubscribe_state = broadcaster.subscribe(_enqueue_threadsafe)
    unsubscribe_confirm = confirmations.subscribe(_enqueue_threadsafe)
    fanout = asyncio.create_task(_fanout_forever())
    try:
        yield
    finally:
        unsubscribe_state()
        unsubscribe_confirm()
        fanout.cancel()


app = FastAPI(lifespan=_lifespan)


@app.middleware("http")
async def no_cache(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


# ---------- the one interaction pipeline ----------

def _respond(text: str) -> str:
    """Blocking: user text -> transcript -> brain -> transcript -> speech.
    Runs in the threadpool; states are emitted by brain and voice_manager."""
    _enqueue_threadsafe({"type": "transcript", "who": "user", "text": text})
    reply = jarvis_brain.think(text)
    _enqueue_threadsafe({"type": "transcript", "who": "jarvis", "text": reply})
    voice_manager.speak(reply)
    return reply


# ---------- endpoints ----------

@app.get("/api/state")
def get_state() -> JSONResponse:
    return JSONResponse({"state": broadcaster.current.value})


@app.post("/api/listen")
async def api_listen() -> JSONResponse:
    """Push-to-talk: capture one utterance, then run the pipeline on it."""
    if not _busy.acquire(blocking=False):
        return JSONResponse({"error": "busy"}, status_code=409)
    try:
        transcript = await run_in_threadpool(voice_manager.listen)
        if not transcript:
            return JSONResponse({"transcript": None, "reply": None})
        reply = await run_in_threadpool(_respond, transcript)
        return JSONResponse({"transcript": transcript, "reply": reply})
    finally:
        _busy.release()


async def _run_chat(text: str, ws: WebSocket) -> None:
    """Fire-and-forget chat runner. The WS receive loop must stay free while
    this blocks (a CONFIRM-gated action waits on the user's answer, and that
    answer arrives through the receive loop — awaiting here would deadlock)."""
    if not _busy.acquire(blocking=False):
        try:
            await ws.send_json({"type": "error", "message": "busy"})
        except Exception:
            pass
        return
    try:
        await run_in_threadpool(_respond, text)
    finally:
        _busy.release()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _clients.add(ws)
    try:
        # State sync so a (re)connecting HUD renders the truth immediately —
        # including a confirm modal that was pending when the tab (re)loaded.
        await ws.send_json({"type": "state", "state": broadcaster.current.value,
                            "seq": 0, "detail": "sync"})
        pending = confirmations.pending_event()
        if pending:
            await ws.send_json(pending)
        # Mid-chain (re)connect: replay the chain so the strip re-renders
        # (mirrors the confirm replay above).
        tracker = chain.current()
        if tracker and (tracker.steps or tracker.calls):
            await ws.send_json(tracker.snapshot())
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "chat":
                text = (msg.get("text") or "").strip()[:MAX_CHAT_CHARS]
                if not text:
                    continue
                task = asyncio.create_task(_run_chat(text, ws))
                _chat_tasks.add(task)
                task.add_done_callback(_chat_tasks.discard)
            elif kind == "confirm_response":
                confirmations.resolve(str(msg.get("id", "")),
                                      bool(msg.get("approved")))
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)


@app.get("/")
def index():
    page = STATIC_DIR / "index.html"
    if page.exists():
        return FileResponse(page)
    return JSONResponse({"status": "HUD not built yet — stage 5"})


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
