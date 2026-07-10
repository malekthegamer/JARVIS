"""Slice-8 Stage-4 tests: tab enumeration + close_tabs, against a REAL but
ISOLATED Chrome (throwaway --user-data-dir profile, uniquely-marked tab
titles) so the user's own browser is never touched. Needs Chrome installed
(resolved via the same Start-Menu/App-Paths path launch_app uses)."""
from __future__ import annotations

import subprocess
import tempfile
import time

import pytest

from jarvis.primitives import apps, tabs

CHROME, _ = apps.resolve_app("chrome")
pytestmark = pytest.mark.skipif(CHROME is None, reason="Chrome not installed")

# Unique markers so tests NEVER match the user's real browser windows.
T1, T2, T3 = "JTabAlpha", "JTabBeta", "JTabKeepMe"
HINT = "JTab"


def _data_url(title: str) -> str:
    return f"data:text/html,<title>{title}</title>{title}"


@pytest.fixture()
def chrome3():
    """A fresh isolated 3-tab Chrome window; killed (with profile) after."""
    profile = tempfile.mkdtemp(prefix="jarvis_tabtest_")
    proc = subprocess.Popen([
        CHROME, f"--user-data-dir={profile}", "--no-first-run",
        "--disable-session-crashed-bubble", "--new-window",
        _data_url(T1), _data_url(T2), _data_url(T3)])
    deadline = time.time() + 20
    while time.time() < deadline:
        r = tabs.list_tabs(HINT)
        if r["ok"] and len(r["tabs"]) == 3:
            break
        time.sleep(0.5)
    else:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True)
        pytest.fail(f"isolated Chrome never showed 3 tabs: {tabs.list_tabs(HINT)}")
    yield HINT
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                   capture_output=True)
    time.sleep(0.5)


def test_list_tabs_enumerates_titles(chrome3):
    r = tabs.list_tabs(chrome3)
    assert r["ok"], r
    joined = " | ".join(r["tabs"])
    for t in (T1, T2, T3):
        assert t in joined, (t, r["tabs"])
    assert "3" in r["message"]


def test_close_single_tab_verified(chrome3):
    r = tabs.close_tabs(window_hint=chrome3, close_matching=T2)
    assert r["ok"], r
    assert r["closed"] == 1
    left = tabs.list_tabs(chrome3)
    assert len(left["tabs"]) == 2
    assert all(T2 not in t for t in left["tabs"]), left


def test_close_tabs_keep_filter_description(chrome3):
    d = tabs.describe_close_tabs({"window": chrome3, "keep_matching": T3})
    assert d is not None
    assert "2" in d, d                      # closes exactly 2
    assert T3 in d, d                       # names what is KEPT
    assert "window" not in d.lower() or "close" in d.lower()


def test_keep_filter_closes_only_others(chrome3):
    r = tabs.close_tabs(window_hint=chrome3, keep_matching=T3)
    assert r["ok"] and r["closed"] == 2, r
    left = tabs.list_tabs(chrome3)
    assert len(left["tabs"]) == 1 and T3 in left["tabs"][0], left


def test_keep_matching_nothing_warns_all(chrome3):
    d = tabs.describe_close_tabs({"window": chrome3,
                                  "keep_matching": "zzz-no-such-tab"})
    assert d is not None
    assert "NONE" in d or "none" in d, d
    assert "3" in d, d
    assert "window" in d.lower(), d         # names the window-close escalation


def test_last_tab_description_names_window_close(chrome3):
    # trim to one tab first
    assert tabs.close_tabs(window_hint=chrome3, keep_matching=T3)["ok"]
    d = tabs.describe_close_tabs({"window": chrome3, "close_matching": T3})
    assert d is not None
    assert "window" in d.lower(), d         # closing the last tab = window closes


def test_close_matching_nothing_no_modal(chrome3):
    d = tabs.describe_close_tabs({"window": chrome3,
                                  "close_matching": "zzz-no-such-tab"})
    assert d is None, "no matching tabs -> no pointless modal (close_window precedent)"


def test_no_browser_no_modal_clean_fail():
    d = tabs.describe_close_tabs({"window": "zzz-no-such-browser-window",
                                  "close_matching": "anything"})
    assert d is None
    r = tabs.close_tabs(window_hint="zzz-no-such-browser-window",
                        close_matching="anything")
    assert not r["ok"]
    assert "window" in r["message"].lower() or "browser" in r["message"].lower()


def test_vanished_tab_skipped_honestly(chrome3):
    """A tab that disappears between gate-time and execution is skipped and
    reported — never guessed at (risk-register race case)."""
    d = tabs.describe_close_tabs({"window": chrome3, "keep_matching": T3})
    assert d is not None and "2" in d
    # the race: T2 vanishes after the description was computed
    assert tabs.close_tabs(window_hint=chrome3, close_matching=T2)["ok"]
    r = tabs.close_tabs(window_hint=chrome3, keep_matching=T3)
    assert r["ok"], r
    assert r["closed"] == 1, r              # only T1 was still there
    left = tabs.list_tabs(chrome3)
    assert len(left["tabs"]) == 1 and T3 in left["tabs"][0]


def test_close_tabs_gate_walk(chrome3):
    """Through the REAL executor + gate: declined -> nothing closes;
    approved -> tabs close (registry tier/describe wiring)."""
    import threading
    import time as _time

    from jarvis import primitives
    from jarvis.core.confirmations import confirmations

    def resolver(approved):
        def responder(event):
            if event.get("type") == "confirm_request":
                threading.Thread(target=lambda: (
                    _time.sleep(0.05),
                    confirmations.resolve(event["id"], approved))).start()
        return confirmations.subscribe(responder)

    unsub = resolver(False)
    try:
        out = primitives.execute("close_tabs",
                                 {"window": chrome3, "keep_matching": T3})
    finally:
        unsub()
    assert "CANCELLED" in out
    assert len(tabs.list_tabs(chrome3)["tabs"]) == 3, "decline must close nothing"

    unsub = resolver(True)
    try:
        out = primitives.execute("close_tabs",
                                 {"window": chrome3, "keep_matching": T3})
    finally:
        unsub()
    assert out.startswith("OK"), out
    assert len(tabs.list_tabs(chrome3)["tabs"]) == 1
