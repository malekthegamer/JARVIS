"""JARVIS entry point: start the server and open the HUD.

    python run.py            # serve + open browser
    python run.py --no-open  # serve only
"""
from __future__ import annotations

import sys
import threading
import webbrowser

import uvicorn

from jarvis import config


def main() -> None:
    url = f"http://{config.SERVER_HOST}:{config.SERVER_PORT}"
    if "--no-open" not in sys.argv:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    print(f"JARVIS HUD: {url}")
    uvicorn.run("jarvis.server:app", host=config.SERVER_HOST,
                port=config.SERVER_PORT, log_level="warning")


if __name__ == "__main__":
    main()
