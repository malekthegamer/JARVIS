"""JARVIS system-tray app (slice 13) — minimal background presence.

    python -m jarvis.tray

Runs the FastAPI server in a background thread and shows a tray icon with:
  - Open HUD            (opens the dashboard in the browser)
  - Wake-word listening (checkbox: toggles the "hey jarvis" listener + persists)
  - Quit
The icon tooltip reflects the live agent state. Deliberately minimal — no
settings UI (that is the HUD's job). `run.py` is unchanged; this is an
additional launcher.
"""
from __future__ import annotations

import os
import threading
import time
import webbrowser

from jarvis import config
from jarvis.state import AgentState

HUD_URL = f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/"

_STATUS = {
    AgentState.IDLE: "online",
    AgentState.LISTENING: "listening…",
    AgentState.THINKING: "thinking…",
    AgentState.CONFIRMING: "waiting for confirm",
    AgentState.EXECUTING: "working…",
    AgentState.SPEAKING: "speaking…",
}


def _status_text(state: AgentState) -> str:
    return f"JARVIS — {_STATUS.get(state, 'online')}"


def _make_icon_image():
    """The arc-reactor mark (matches the HUD orb palette)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (11, 16, 22, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 56, 56), outline=(92, 200, 255, 255), width=5)
    d.ellipse((24, 24, 40, 40), fill=(92, 200, 255, 255))
    return img


def open_hud(icon=None, item=None) -> None:
    webbrowser.open(HUD_URL)


def toggle_wake() -> bool:
    """Flip the wake listener on/off and PERSIST the choice (so it survives a
    restart). Returns the new running state. Never raises."""
    from jarvis import server
    from jarvis.core.settings_store import settings
    if server.wake_running():
        settings.set("wake.enabled", False)
        server.stop_wake()
        return False
    settings.set("wake.enabled", True)
    server.start_wake()
    return server.wake_running()


def _wake_checked(item) -> bool:
    from jarvis import server
    return server.wake_running()


def open_my_browser() -> None:
    """Start the Chrome profile JARVIS can actually drive (slice 39).

    Clicking the ordinary Chrome icon opens the DEFAULT profile, which Chrome
    136+ refuses to expose over the debug protocol — JARVIS could never attach
    to it. Starting it from here means the browser you use all day is the same
    one JARVIS drives. Never raises into the tray loop."""
    try:
        from jarvis.primitives import web
        web.launch_daily_browser()
    except Exception:
        pass


def build_menu():
    import pystray
    from pystray import MenuItem as Item
    return pystray.Menu(
        Item("Open HUD", open_hud, default=True),
        Item("Open my browser", lambda icon, item: open_my_browser()),
        Item("Wake-word listening", lambda icon, item: toggle_wake(),
             checked=_wake_checked),
        Item("Quit", lambda icon, item: icon.stop()),
    )


def build_icon():
    import pystray
    return pystray.Icon("JARVIS", _make_icon_image(),
                        _status_text(AgentState.IDLE), build_menu())


# ---------------------------------------------------------------- server ---

def _ensure_std_streams() -> None:
    """Give the process real stdout/stderr when launched by pythonw.exe.

    THE Desktop-shortcut bug (v1.0.4). pythonw.exe runs with no console, so
    sys.stdout and sys.stderr are None. uvicorn's log formatter does
    `sys.stdout.isatty()` while configuring logging, which raises
    AttributeError INSIDE the server thread — the server dies before binding
    the port, and the tray reports the misleading "server did not come up
    within 15s (is something already using port 8000?)". The port was free the
    whole time.

    Any `print()` in the imported modules would fail the same way, so this is
    repaired process-wide, not just for uvicorn."""
    import sys
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


# The server thread's traceback, if it died. v1.0.4 taught that a daemon
# thread swallows tracebacks — and _ensure_std_streams() points stderr at
# os.devnull, so under pythonw there was literally nowhere for it to go. The
# launcher then ASKED "did startup crash?" because it could not know. Now it
# can.
_server_error: str | None = None


def _run_server() -> None:
    global _server_error
    _ensure_std_streams()          # MUST precede uvicorn's logging config
    try:
        import uvicorn
        uvicorn.run("jarvis.server:app", host=config.SERVER_HOST,
                    port=config.SERVER_PORT, log_level="warning")
    except BaseException:          # noqa: BLE001 — a swallowed crash is the bug
        import traceback
        _server_error = traceback.format_exc()
        try:
            path = config.DATA_DIR / "server_error.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_server_error, encoding="utf-8")
        except Exception:
            pass                   # logging the crash must never mask it


def _wait_for_server(timeout: float = 120.0, thread=None) -> bool:
    """Wait for the server to answer /api/state.

    v1.0.6 — THE "JARVIS could not start" on every boot. This used to allow a
    fixed 15s, which was a GUESS at cold-start cost and simply wrong: measured
    on one idle machine, minutes apart, the same launch took **17.6s cold** and
    **3.3s warm**. Every boot is cold by definition and competes with every
    other startup app, so autostart failed EVERY time while a later
    double-click always worked — and the failure killed the process, so JARVIS
    never came up at all.

    So don't time it, watch it: keep waiting while the server thread is ALIVE,
    and bail the moment it DIES. A slow start is not a failure; a dead thread
    is, and it needs no waiting at all. The remaining timeout is only a
    backstop against a wedged thread."""
    import urllib.request
    url = f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/api/state"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            pass
        if thread is not None and not thread.is_alive():
            return False           # it will never answer — don't sit out the clock
        time.sleep(0.3)
    return False


def _port_holder() -> str | None:
    """'name (pid N)' of whatever is listening on our port, or None."""
    try:
        import psutil
        for conn in psutil.net_connections(kind="inet"):
            if (conn.status == psutil.CONN_LISTEN and conn.laddr
                    and conn.laddr.port == config.SERVER_PORT):
                if not conn.pid:
                    return "another process"
                try:
                    return f"{psutil.Process(conn.pid).name()} (pid {conn.pid})"
                except Exception:
                    return f"pid {conn.pid}"
    except Exception:
        return None                # needs privileges on some systems — don't guess
    return None


def _startup_failure_reason() -> str:
    """Say what actually happened. The old message asked the USER to guess
    ("is something already using port 8000, or did startup crash?") — and in
    v1.0.4 that guess was wrong and cost real debugging time."""
    if _server_error:
        return ("the server thread crashed during startup:\n\n"
                f"{_server_error}\n"
                f"(also saved to {config.DATA_DIR / 'server_error.log'})")
    holder = _port_holder()
    if holder:
        return (f"port {config.SERVER_PORT} is already in use by {holder}. "
                f"Close it, or change the port in settings.")
    return (f"the server is still starting and had not answered after "
            f"{int(_STARTUP_TIMEOUT_S)}s — unusually slow, but nothing looks "
            f"broken (the server thread is alive and port "
            f"{config.SERVER_PORT} is free).")


_STARTUP_TIMEOUT_S = 120.0


def main() -> None:
    server_thread = threading.Thread(target=_run_server, name="jarvis-server",
                                     daemon=True)
    server_thread.start()
    if not _wait_for_server(timeout=_STARTUP_TIMEOUT_S, thread=server_thread):
        # Raise, don't return: run_guarded() turns this into a visible dialog +
        # log. A silent return under pythonw is exactly the "shortcut does
        # nothing" failure.
        raise RuntimeError("JARVIS's server did not start — "
                           + _startup_failure_reason())
    print(f"JARVIS tray running. HUD: {HUD_URL}")

    icon = build_icon()

    from jarvis.state import broadcaster

    def _on_state(event: dict) -> None:
        if event.get("type") != "state":
            return
        try:
            icon.title = _status_text(AgentState(event["state"]))
        except Exception:
            pass

    unsub = broadcaster.subscribe(_on_state)
    try:
        icon.run()  # blocks the main thread until Quit
    finally:
        unsub()
        from jarvis import server
        server.stop_wake()


def run_guarded(main_fn=main) -> None:
    """Entry point for the double-click Desktop shortcut (via tray_start.pyw).

    The shortcut runs pythonw.exe, which has NO console — so any unhandled
    exception makes the shortcut fail SILENTLY (the "it just doesn't work"
    report). This writes the traceback to data/tray_error.log and shows a
    Windows dialog, so a failed launch is always diagnosable. Returns normally
    on success; re-raises after logging so `python -m` callers still see it."""
    _ensure_std_streams()   # before ANY print(): pythonw has no stdout at all
    try:
        main_fn()
    except BaseException:  # noqa: BLE001 — a launcher must surface EVERYTHING
        import traceback
        log_path = None
        try:
            log_path = config.DATA_DIR / "tray_error.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            log_path = None
        try:
            import ctypes
            where = f"\n\nDetails written to:\n{log_path}" if log_path else ""
            ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
                0, "JARVIS could not start." + where, "JARVIS", 0x10)
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
