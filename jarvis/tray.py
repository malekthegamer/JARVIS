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


def build_menu():
    import pystray
    from pystray import MenuItem as Item
    return pystray.Menu(
        Item("Open HUD", open_hud, default=True),
        Item("Wake-word listening", lambda icon, item: toggle_wake(),
             checked=_wake_checked),
        Item("Quit", lambda icon, item: icon.stop()),
    )


def build_icon():
    import pystray
    return pystray.Icon("JARVIS", _make_icon_image(),
                        _status_text(AgentState.IDLE), build_menu())


# ---------------------------------------------------------------- server ---

def _run_server() -> None:
    import uvicorn
    uvicorn.run("jarvis.server:app", host=config.SERVER_HOST,
                port=config.SERVER_PORT, log_level="warning")


def _wait_for_server(timeout: float = 15.0) -> bool:
    import urllib.request
    url = f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/api/state"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main() -> None:
    threading.Thread(target=_run_server, name="jarvis-server", daemon=True).start()
    if not _wait_for_server():
        # Raise, don't return: run_guarded() turns this into a visible dialog +
        # log. A silent return under pythonw is exactly the "shortcut does
        # nothing" failure.
        raise RuntimeError(
            "the JARVIS server did not come up within 15s "
            "(is something already using port "
            f"{config.SERVER_PORT}, or did startup crash?)")
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
