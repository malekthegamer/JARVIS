"""Shared port helper.

`config.SERVER_PORT` is a hardcoded constant and the CORS allowlist is built
from it at import time, so any test that starts a REAL JARVIS server must own
that exact port. Two files now need it (test_extension_browser.py and
test_entrypoint_smoke.py), hence one helper instead of two copies.
"""
from __future__ import annotations

import socket


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def port_holder(port: int) -> str:
    """Who owns `port`, as 'name (pid N)'. Mirrors tray._port_holder so the test
    failure names the culprit instead of leaving it to be guessed."""
    try:
        import os

        import psutil
        for conn in psutil.net_connections(kind="inet"):
            if (conn.status == psutil.CONN_LISTEN and conn.laddr
                    and conn.laddr.port == port and conn.pid):
                proc = psutil.Process(conn.pid)
                mine = " — THIS pytest process" if conn.pid == os.getpid() else ""
                return f"{proc.name()} (pid {conn.pid}){mine}"
    except Exception:
        pass
    return "an unidentified process"


def busy_port_message(port: int, why: str) -> str:
    """The actionable failure. A test that just says 'connection refused' costs
    an investigation; this one says exactly what to do (proven in the slice-45
    full-suite run, where 18 errors were diagnosed instantly from this text).

    Two genuinely different causes, so the message distinguishes them:
      * the OWNER's JARVIS is running   -> quit it
      * a previous test file still holds it -> test_extension_browser.py starts
        uvicorn in a daemon thread it never shuts down, so it owns the port for
        the REST of the pytest process. Alphabetical collection puts
        test_entrypoint_smoke.py first, which is why the full suite works; run
        them in the other order and it will not.
    """
    holder = port_holder(port)
    hint = ("a previous test file in this run still holds it (test_extension_"
            "browser.py leaks its uvicorn thread) — run this file first or alone"
            if "THIS pytest process" in holder else
            "quit the running JARVIS (tray icon → Quit, or Stop-Process it)")
    return (f"port {port} is held by {holder}. {hint}, then re-run. {why}")
