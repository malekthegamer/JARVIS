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
from jarvis.core.settings_store import settings
from jarvis.state import AgentState, broadcaster
from jarvis.voice import wake
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


# ---------- telemetry (slice 7, spec §2.3) ----------
# Sampled ONLY while a HUD is connected. Events go straight into the WS
# queue (the transcript path) — deliberately NOT through the broadcaster,
# so the state seq stream stays pure.

_gpu_last: dict = {}


def _sample_telemetry() -> dict:
    """CPU/RAM/foreground title. Runs in a worker thread; never raises."""
    import psutil
    event: dict = {"type": "telemetry"}
    try:
        event["cpu"] = psutil.cpu_percent(None)
        vm = psutil.virtual_memory()
        event["ram"] = vm.percent
        event["ram_used_gb"] = round(vm.used / 2**30, 1)
        event["ram_total_gb"] = round(vm.total / 2**30, 1)
    except Exception:
        pass
    try:
        import win32gui
        event["window"] = win32gui.GetWindowText(
            win32gui.GetForegroundWindow())[:80]
    except Exception:
        pass
    return event


def _sample_gpu() -> dict | None:
    """nvidia-smi query (~90ms subprocess). None when unavailable."""
    import subprocess
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3)
        if out.returncode != 0:
            return None
        util, used, total = (x.strip() for x in
                             out.stdout.strip().splitlines()[0].split(","))
        return {"gpu": float(util),
                "gpu_mem_used_gb": round(int(used) / 1024, 1),
                "gpu_mem_total_gb": round(int(total) / 1024, 1)}
    except Exception:
        return None


async def _telemetry_forever() -> None:
    tick = 0
    while True:
        try:
            interval = float(settings.get("telemetry.interval_s", 2.0))
        except Exception:
            interval = 2.0
        await asyncio.sleep(max(interval, 0.05))
        if not _clients or not settings.get("telemetry.enabled", True):
            continue
        try:
            event = await asyncio.to_thread(_sample_telemetry)
            if tick % 3 == 0:  # GPU is a subprocess — sample sparsely, cache
                gpu = await asyncio.to_thread(_sample_gpu)
                if gpu:
                    _gpu_last.update(gpu)
            event.update(_gpu_last)
            _enqueue_threadsafe(event)
        except Exception:
            pass  # telemetry must never take the server down
        tick += 1


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
    telemetry = asyncio.create_task(_telemetry_forever())
    start_wake()  # no-op unless wake.enabled
    try:
        yield
    finally:
        stop_wake()
        try:
            from jarvis.primitives import web
            web.session.close()  # never leave an orphan browser
        except Exception:
            pass
        unsubscribe_state()
        unsubscribe_confirm()
        fanout.cancel()
        telemetry.cancel()


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


# ---------- wake word (slice 13) ----------
# The wake listener funnels a triggered utterance through the SAME _busy lock
# and _respond pipeline as push-to-talk and WS chat — so the two triggers
# coexist and never stack. A wake that lands while an interaction is in flight
# is dropped; a wake with no real follow-up returns to IDLE quietly.

_wake_listener: wake.WakeListener | None = None


def _on_wake() -> None:
    """Called (on the listener thread) when the wake word fires. The listener
    has already released the mic, so the follow-up capture owns it alone."""
    if not _busy.acquire(blocking=False):
        return  # PTT/chat/confirm mid-flight — drop this trigger, never stack
    try:
        from jarvis.core.settings_store import settings as _s
        timeout = float(_s.get("wake.follow_up_timeout_s", 5))
        wake.handle_wake(
            listen=lambda t: voice_manager.listen(timeout=t),
            respond=_respond,
            set_idle=lambda: broadcaster.set(AgentState.IDLE),
            timeout_s=timeout)
    finally:
        _busy.release()


def wake_running() -> bool:
    return bool(_wake_listener and _wake_listener.running)


def start_wake() -> None:
    """Start the always-on wake listener if enabled. Idempotent; never raises."""
    global _wake_listener
    if not settings.get("wake.enabled", False):
        return
    if wake_running():
        return
    try:
        _wake_listener = wake.WakeListener(
            on_wake=_on_wake,
            threshold=float(settings.get("wake.threshold", 0.5)),
            cooldown_s=float(settings.get("wake.cooldown_s", 2.0)))
        _wake_listener.start()
        print("  [wake] listening for 'hey jarvis'")
    except Exception as exc:
        print(f"  [wake] could not start: {exc}")
        _wake_listener = None


def stop_wake() -> None:
    global _wake_listener
    if _wake_listener is not None:
        try:
            _wake_listener.stop()
        except Exception:
            pass
        _wake_listener = None


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
