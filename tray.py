"""JARVIS desktop app — the dashboard in a native window + a system-tray icon.

    python tray.py

Runs the FastAPI server on a background thread, opens the dashboard in a
pywebview window (Windows WebView2 — no Electron), and adds a pystray icon with
Open Dashboard / Toggle Wake-Word / Quit. Pure Python, no build step.
"""
from __future__ import annotations

import threading
import time

import uvicorn

import config
from core import audit_log

_wake_thread: threading.Thread | None = None
_wake_stop = threading.Event()


# ---------------------------------------------------------------- server ---
def _run_server() -> None:
    from server import app
    uvicorn.run(app, host=config.SERVER_HOST, port=config.SERVER_PORT, log_level="warning")


def _wait_for_server(timeout: float = 15.0) -> bool:
    import urllib.request
    url = f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/api/status"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


# ----------------------------------------------------------- wake listener ---
def _wake_loop() -> None:
    """Optional always-on wake-word listener, toggled from the tray."""
    from brain import jarvis_brain
    from core import skill_registry
    from core.settings_store import settings
    from voice.voice_manager import voice_manager

    skill_registry.load_all()
    wake_word = str(settings.get("wake_word", "jarvis")).lower()
    while not _wake_stop.is_set():
        try:
            heard = voice_manager.listen(timeout=6)
            if not heard:
                continue
            lower = heard.lower()
            if wake_word not in lower:
                continue
            request = heard[lower.find(wake_word) + len(wake_word):].strip(" ,.!?") or "yes?"
            reply = jarvis_brain.think(request)
            voice_manager.speak(reply)
        except Exception:
            time.sleep(0.5)


def _toggle_wake(icon, item) -> None:
    global _wake_thread
    if _wake_thread and _wake_thread.is_alive():
        _wake_stop.set()
        _wake_thread = None
        audit_log.log_action("tray", "wake_word", {"state": "off"})
    else:
        _wake_stop.clear()
        _wake_thread = threading.Thread(target=_wake_loop, name="jarvis-wake", daemon=True)
        _wake_thread.start()
        audit_log.log_action("tray", "wake_word", {"state": "on"})


def _wake_active(item) -> bool:
    return bool(_wake_thread and _wake_thread.is_alive())


# ------------------------------------------------------------------- icon ---
def _make_icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (11, 16, 22, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 56, 56), outline=(92, 200, 255, 255), width=5)
    d.ellipse((24, 24, 40, 40), fill=(92, 200, 255, 255))
    return img


def _open_dashboard(icon=None, item=None) -> None:
    import webbrowser
    webbrowser.open(f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/")


def _build_tray():
    import pystray
    from pystray import MenuItem as Item
    menu = pystray.Menu(
        Item("Open Dashboard", _open_dashboard, default=True),
        Item("Wake-word listening", _toggle_wake, checked=_wake_active),
        Item("Quit", lambda icon, item: icon.stop()),
    )
    return pystray.Icon("JARVIS", _make_icon_image(), "JARVIS", menu)


# ------------------------------------------------------------------- main ---
def main() -> None:
    threading.Thread(target=_run_server, name="jarvis-server", daemon=True).start()
    if not _wait_for_server():
        print("Server didn't start in time.")
        return

    tray = _build_tray()
    threading.Thread(target=tray.run, name="jarvis-tray", daemon=True).start()

    try:
        import webview  # pywebview
        window = webview.create_window(
            "JARVIS", f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/",
            width=1200, height=800, min_size=(800, 600),
        )
        webview.start()
    except Exception:
        # No WebView2 / pywebview — fall back to the browser and idle on the tray.
        _open_dashboard()
        while getattr(tray, "visible", True):
            time.sleep(1)
    finally:
        _wake_stop.set()
        try:
            tray.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
