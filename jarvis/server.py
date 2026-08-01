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
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from jarvis import config
from jarvis.brain import jarvis_brain
from jarvis.core import chain, desktop, interrupt
from jarvis.core.schedules import schedule_store
from jarvis.core import extbridge as _extbridge_mod
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
    # Slice 42: browser health rides the telemetry event the HUD already
    # receives every ~2s. The extension dying used to be INVISIBLE — JARVIS
    # simply started behaving oddly (opening new windows, typing URLs by hand)
    # with nothing on screen to explain it.
    try:
        event["browser_mode"] = str(
            settings.get("web.profile_mode", "isolated") or "isolated").lower()
        event["browser_connected"] = _extbridge_mod.bridge.connected()
    except Exception:
        event["browser_mode"] = "isolated"
        event["browser_connected"] = False
    # Slice 44: WHICH brain answered. A fallback model quietly handling requests
    # would be uptime bought by downgrading quality — visible, or it isn't
    # honest.
    try:
        event["brain_model"] = jarvis_brain.last_model
        event["brain_is_fallback"] = bool(jarvis_brain.last_model_was_fallback)
    except Exception:
        event["brain_model"] = None
        event["brain_is_fallback"] = False
    return event


def _sample_gpu() -> dict | None:
    """nvidia-smi query (~90ms subprocess). None when unavailable."""
    import subprocess
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
            creationflags=config.NO_WINDOW)   # never flash a console
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


async def _extension_heartbeat_forever() -> None:
    """Keep the browser extension's service worker alive (v1.0.7).

    Without this the extension died after ~30s idle and only came back on its
    once-a-minute alarm, so JARVIS could reach the user's real Chrome only
    about half the time. 20s is comfortably inside the kill window."""
    while True:
        await asyncio.sleep(20)
        try:
            await _extbridge_mod.bridge.heartbeat()
        except Exception:
            pass          # a keepalive must never take the server down


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



# ---------- scheduled routines (slice 50) ----------
# A schedule fires a SAVED ROUTINE, unattended. Two things make that safe:
#   * chain.start(unattended=True) -> any non-AUTO step is PARKED, never
#     prompted at an empty room and never auto-approved.
#   * the guards below -> it never interrupts the user or steals focus.
# Measured (Stage 0): realistic routines resolve 4/4 AUTO, so this is not a
# feature that spends its life parking.

SCHEDULER_TICK_S = 60      # a minute's resolution is what "08:00" means


def _run_scheduled(record: dict) -> None:
    """Run one due schedule's routine with nobody watching.

    Holds _busy for the duration, exactly like every other trigger, so a
    scheduled run and a user interaction can never overlap.
    """
    routine = record["routine"]
    chain.start(unattended=True)
    try:
        from jarvis import primitives   # lazy: server imports before primitives
        result = primitives.execute("run_routine", {"name": routine})
        print(f"  [schedule] {routine}: {str(result)[:160]}")
        if record.get("announce"):
            try:
                voice_manager.speak(f"Your {routine} routine has run.")
            except Exception:
                pass       # a silent announcement is not a failed run
    finally:
        chain.clear("done")


def _scheduler_tick(now=None) -> None:
    """One pass. Separated from the loop so tests drive it with a fake clock
    instead of waiting real minutes."""
    from datetime import datetime
    if not settings.get("schedules.enabled", True):
        return
    now = now or datetime.now()
    grace = int(settings.get("schedules.grace_minutes", 60))

    try:
        due = schedule_store.due(now, grace_minutes=grace)
    except Exception:
        return
    if not due:
        return

    # Guards, in cheapest-first order. A skip must NOT consume the window --
    # the job stays due, so it can still run once the user is free.
    if desktop.screen_is_claimed():
        print(f"  [schedule] {len(due)} due, skipped: a fullscreen app is up")
        return
    if not _busy.acquire(blocking=False):
        print(f"  [schedule] {len(due)} due, skipped: an interaction is in flight")
        return
    try:
        for record in due:
            # Stamp BEFORE running: a crash mid-routine must not leave the job
            # looking un-run, or the next tick fires it again (risk 3).
            try:
                schedule_store.mark_ran_id(record["id"], now)
            except Exception:
                pass
            try:
                _run_scheduled(record)
            except Exception as exc:
                # One bad routine must never kill the scheduler (risk 8).
                print(f"  [schedule] {record.get('routine')!r} failed: {exc}")
    finally:
        _busy.release()


async def _scheduler_forever() -> None:
    """The timer. Same shape and same discipline as _telemetry_forever: an
    exception here must never take the server down."""
    while True:
        await asyncio.sleep(SCHEDULER_TICK_S)
        try:
            await run_in_threadpool(_scheduler_tick)
        except Exception:
            pass


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _loop, _events
    _loop = asyncio.get_running_loop()
    _events = asyncio.Queue()
    unsubscribe_state = broadcaster.subscribe(_enqueue_threadsafe)
    unsubscribe_confirm = confirmations.subscribe(_enqueue_threadsafe)
    fanout = asyncio.create_task(_fanout_forever())
    telemetry = asyncio.create_task(_telemetry_forever())
    heartbeat = asyncio.create_task(_extension_heartbeat_forever())
    scheduler = asyncio.create_task(_scheduler_forever())
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
        heartbeat.cancel()
        scheduler.cancel()


app = FastAPI(lifespan=_lifespan)


# ---------- slice 36: the Origin guard (the HUD transport is UNAUTHENTICATED) ----------
#
# The server binds to 127.0.0.1, which stops the network — but NOT the browser.
# WebSockets are exempt from the same-origin policy, so before this guard any
# page the user visited could open ws://127.0.0.1:8000/ws, receive every
# broadcast confirm_request (id included, see _pump -> _clients) and reply
# {"type":"confirm_response","approved":true} — approving its OWN prompt and
# defeating the CONFIRM gate, the one control standing in front of run_shell,
# delete_path and send_email. Verified live before the fix.
#
# The rule: reject when Origin is PRESENT and foreign; permit when ABSENT.
# Browsers always send Origin on a WS handshake and on cross-origin HTTP, so
# this closes the entire browser attack surface. A local non-browser client
# (pytest, the harnesses, curl) is already arbitrary local code execution, so
# gating it would buy nothing and only break tooling. Test-pinned both ways.
_ALLOWED_ORIGINS = frozenset({
    f"http://{config.SERVER_HOST}:{config.SERVER_PORT}",
    f"http://localhost:{config.SERVER_PORT}",
})


def _origin_ok(headers) -> bool:
    """True if this request may drive the agent. Fails CLOSED on anything
    unrecognized, matching the project's unknown -> refuse doctrine."""
    if (headers.get("sec-fetch-site") or "").lower() == "cross-site":
        return False                      # defence in depth; browsers set this
    origin = headers.get("origin")
    if origin is None:
        return True                       # non-browser client (see above)
    return origin in _ALLOWED_ORIGINS


# Safe methods never change state, so they don't need the CSRF/Origin guard —
# and guarding them was a real v1.0.0 bug: a cross-site GET (e.g. reaching the
# HUD via a redirect, which stamps Sec-Fetch-Site: cross-site on an ordinary
# page load) was refused, breaking the HUD entirely. A cross-origin page still
# cannot READ any response — the same-origin policy blocks that because we send
# no Access-Control-Allow-Origin — so serving a harmless GET is correct. The
# guard stays on state-changing methods here, and on the WebSocket (which
# bypasses CORS) in ws_endpoint.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@app.middleware("http")
async def guard_and_no_cache(request, call_next):
    if request.method not in _SAFE_METHODS and not _origin_ok(request.headers):
        return JSONResponse({"error": "cross-origin request refused"},
                            status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


# ---------- the one interaction pipeline ----------

# Slice 18: the explicit per-request dry-run trigger. Parsed HERE — the one
# funnel all three inputs (WS chat, push-to-talk, wake word) share — so the
# flag is set mechanically, never by the model. Matches a leading
# "dry run:" / "dry-run" / "dryrun," etc.; deliberately NOT mid-sentence
# (the prompt teaches the model to suggest the prefix instead).
_DRY_RUN_RE = re.compile(r"^\s*dry[- ]?run\b[:,]?\s*", re.IGNORECASE)


def parse_dry_run(text: str) -> tuple[bool, str]:
    """(dry_run, remaining_text). Pure — unit-testable without a server."""
    m = _DRY_RUN_RE.match(text or "")
    if not m:
        return False, text
    return True, text[m.end():]


def _respond(text: str) -> str:
    """Blocking: user text -> transcript -> brain -> transcript -> speech.
    Runs in the threadpool; states are emitted by brain and voice_manager."""
    dry_run, text = parse_dry_run(text)
    _enqueue_threadsafe({"type": "transcript", "who": "user", "text": text})
    reply = jarvis_brain.think(text, dry_run=dry_run)
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
        # Slice 49 barge-in. An interaction is mid-flight. If JARVIS is TALKING
        # or ACTING, the wake word means "stop" — so interrupt instead of
        # dropping the trigger, which is what made it impossible to cut JARVIS
        # off. It stops and RETURNS: capturing a follow-up here would re-enter
        # _busy and stack triggers, the exact thing this lock exists to prevent.
        #
        # CONFIRMING is deliberately excluded: that modal owns its own answer
        # (Approve/Cancel), and cancelling it out from under the user would be a
        # different action than they asked for.
        #
        # Safe because Stage 0 MEASURED that JARVIS's own TTS peaks at 0.196
        # against the 0.50 wake threshold (381 frames of real speech, 0 trips),
        # so listening while speaking does not make it interrupt itself.
        if broadcaster.current in (AgentState.SPEAKING, AgentState.EXECUTING):
            interrupt.request()
        return  # never stack a second interaction
    try:
        from jarvis.core.settings_store import settings as _s
        from jarvis.voice import tones
        timeout = float(_s.get("wake.follow_up_timeout_s", 5))
        wake.handle_wake(
            listen=lambda t: voice_manager.listen(timeout=t),
            respond=_respond,
            set_idle=lambda: broadcaster.set(AgentState.IDLE),
            timeout_s=timeout,
            # Slice 57: keep the conversation open so a second command needs no
            # second wake word, and cue each capture audibly so the user is
            # never guessing whether it is their turn.
            follow_up_s=float(_s.get("wake.follow_up_window_s", 5)),
            max_turns=int(_s.get("wake.max_follow_up_turns", 6)),
            max_total_s=float(_s.get("wake.max_conversation_s", 90)),
            on_listen_start=lambda: tones.play("listening"))
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


# ---------- settings API (slice 23; surface salvaged from legacy) ----------

_KEYED = ("gemini", "openai", "claude", "openrouter", "elevenlabs")


@app.get("/api/settings")
def get_settings() -> JSONResponse:
    from jarvis import config
    from jarvis.providers import registry
    masked = {name: config.mask_key(config.get_api_key(name)) for name in _KEYED}
    availability = {fam: registry.available(fam) for fam in ("brain", "tts", "stt")}
    return JSONResponse({
        "settings": settings.all(),
        "keys": masked,                                    # secrets NEVER raw here
        "availability": availability,
        "provider_names": {f: registry.names(f) for f in ("brain", "tts", "stt")},
    })


@app.get("/api/setup_state")
def get_setup_state() -> JSONResponse:
    """Slice 37: does this install still need first-run setup?

    BOOLEANS ONLY — the key itself must never cross the wire, even to
    localhost (same posture as the audit viewer, which never decrypts a
    payload just to list records). The HUD uses this to decide whether to
    show the setup panel instead of a silently-dead assistant."""
    from jarvis import config
    try:
        from jarvis.core import embedder
        model_ready = bool(embedder.available())
    except Exception:
        model_ready = False
    return JSONResponse({
        "brain_key": bool(config.get_api_key("gemini")),
        "model_ready": model_ready,
    })


@app.post("/api/settings")
async def post_settings(payload: dict) -> JSONResponse:
    from jarvis import config
    from jarvis.core import audit
    keys = payload.get("keys") or {}
    for provider, value in keys.items():
        if value and "•" not in value:  # ignore masked placeholders echoed back
            config.set_api_key(provider, value)
    new_settings = payload.get("settings") or {}
    wake_before = settings.get("wake.enabled", False)
    if new_settings:
        settings.save(new_settings)      # deep-merge, persist, hot-reload providers
        try:
            audit.audit_log.record(tool="settings", tier="auto", status="ok",
                                   args={"sections": list(new_settings.keys())},
                                   result="settings saved")  # names only, never values
        except Exception:
            pass
    else:
        settings.reload()
    # a wake toggle must actually start/stop the always-on listener
    if settings.get("wake.enabled", False) != wake_before:
        (start_wake if settings.get("wake.enabled", False) else stop_wake)()
    try:
        from jarvis.core import autostart
        autostart.sync_from_settings()
    except Exception:
        pass
    return get_settings()


@app.get("/api/voices")
def get_voices(provider: str = "edge_tts") -> JSONResponse:
    from jarvis.providers import registry
    voices: list[dict] = []
    try:
        if provider == "edge_tts":
            from jarvis.providers.tts.edge_tts_provider import EdgeTTSProvider
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
        from jarvis.voice.capture import (find_real_mic, is_probably_real_mic,
                                          list_input_devices)
        devices = [{"index": i, "name": n, "real": is_probably_real_mic(n)}
                   for i, n in list_input_devices()]
        idx, name = find_real_mic()
        return JSONResponse({"devices": devices, "auto": {"index": idx, "name": name}})
    except Exception as exc:
        return JSONResponse({"devices": [], "auto": None, "error": str(exc)})


@app.post("/api/tts_test")
async def tts_test(payload: dict | None = None) -> JSONResponse:
    text = str((payload or {}).get("text")
               or "Good evening, sir. All systems operational.")
    asyncio.get_running_loop().run_in_executor(
        None, lambda: _safe_speak(text))
    return JSONResponse({"ok": True})


def _safe_speak(text: str) -> None:
    try:
        voice_manager.speak(text)
    except Exception:
        pass


@app.post("/api/stop")
async def api_stop() -> JSONResponse:
    """Barge-in (slice 49): stop speaking and run no further steps.

    No _busy acquisition, by design — see the WS handler. Pressing stop when
    nothing is running is a harmless no-op, reported as interrupted=false rather
    than an error.
    """
    return JSONResponse({"interrupted": interrupt.request()})


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


def _extension_origin_ok(headers) -> bool:
    """True only for the ONE Chrome extension the user configured (slice 41).

    Chrome sends `Origin: chrome-extension://<id>` on the handshake (measured
    in the Stage-0 probe), which `_origin_ok` correctly rejects — the HUD
    socket must stay closed to it. This is a deliberately narrower, separate
    allowlist for the browser socket only.

    EMPTY ID = NOBODY. An unset setting must never read as "allow any
    extension"; that is the unknown-means-refuse doctrine."""
    want = str(settings.get("web.extension_id", "") or "").strip()
    if not want:
        return False
    return (headers.get("origin") or "") == f"chrome-extension://{want}"


@app.websocket("/ws/browser")
async def browser_ws_endpoint(ws: WebSocket) -> None:
    """The JARVIS browser extension's socket — how JARVIS reaches the user's
    REAL everyday Chrome (slice 41; CDP provably cannot, see slice 40).

    DELIBERATELY NOT `/ws`, and this peer is NEVER added to `_clients`.
    That set is broadcast to indiscriminately and carries `confirm_request`
    events INCLUDING their ids — a member can approve its own prompts, which
    is precisely the slice-36 auth bypass. This socket talks to a browser full
    of the user's logged-in sessions and relays untrusted page content, so it
    is the last thing that should ever hold a confirmation id."""
    if not _extension_origin_ok(ws.headers):
        await ws.close(code=1008)      # policy violation; refuse before accept
        return
    await ws.accept()
    _extbridge_mod.bridge.attach(ws, asyncio.get_running_loop())
    try:
        while True:
            msg = await ws.receive_json()
            _extbridge_mod.bridge.deliver(msg)     # resolve whatever is waiting on it
    except WebSocketDisconnect:
        pass
    finally:
        _extbridge_mod.bridge.detach(ws)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    # Slice 36: refuse BEFORE accept() — a rejected peer must never enter
    # _clients, so it never receives a confirm_request id to replay.
    if not _origin_ok(ws.headers):
        await ws.close(code=1008)      # 1008 = policy violation
        return
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
            elif kind == "stop":
                # Slice 49: deliberately does NOT touch _busy. A stop that
                # waited on the lock held by the interaction it is cancelling
                # would deadlock until that interaction finished on its own —
                # i.e. exactly never do its job. The receive loop staying free
                # mid-chain is the same property CONFIRM already relies on.
                interrupt.request()
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


@app.get("/settings")
def settings_page():
    page = STATIC_DIR / "settings.html"
    if page.exists():
        return FileResponse(page)
    return JSONResponse({"status": "settings page not built"}, status_code=404)


# ---------- audit-log viewer (slice 28): READ-ONLY, envelope-first ----------

@app.get("/api/audit")
def get_audit(tail: int = 200) -> JSONResponse:
    """The durable audit timeline — plaintext ENVELOPES only, newest-first.
    Never decrypts the payload (that's /payload, on explicit reveal). Reads
    the audit.audit_log singleton via the module attribute (test-swappable)."""
    from jarvis.core import audit
    envs = audit.audit_log.read_envelopes()
    try:
        n = max(1, min(int(tail), 5000))
    except (TypeError, ValueError):
        n = 200
    rows = list(reversed(envs[-n:]))       # last N, newest first
    return JSONResponse({"records": rows, "total": len(envs)})


@app.get("/api/audit/{index}/payload")
def get_audit_payload(index: int) -> JSONResponse:
    """Decrypt and return ONE record's verbatim args/result — the sole path
    that reveals payload, only when explicitly asked. Out-of-range / undecrypt-
    able records return payload=None + an honest payload_error, never a crash."""
    from jarvis.core import audit
    payload = audit.audit_log.read_payload(index)
    if payload is None:
        return JSONResponse({"payload": None,
                             "payload_error": "that record is no longer "
                                              "available (index out of range)."})
    if "payload_error" in payload and "args" not in payload:
        return JSONResponse({"payload": None,
                             "payload_error": payload["payload_error"]})
    return JSONResponse({"payload": payload})


@app.get("/audit")
def audit_page():
    page = STATIC_DIR / "audit.html"
    if page.exists():
        return FileResponse(page)
    return JSONResponse({"status": "audit page not built"}, status_code=404)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
