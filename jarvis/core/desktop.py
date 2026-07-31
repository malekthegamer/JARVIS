"""Is the screen claimed? (slice 50)

One definition of "the user is mid-game / presenting / fullscreen", shared by
the product and the test suite. It existed only in tests/conftest.py before —
guarding test runs from stealing focus — while the PRODUCT had no such check, so
a scheduled routine would have happily launched apps over a race.

Never raises: any failure reads as "normal desktop", because this guard must
never be the thing that stops a legitimate run.
"""
from __future__ import annotations

import os

# SHQueryUserNotificationState values meaning the screen is claimed:
#   2 = QUNS_BUSY (fullscreen, F11-style)
#   3 = QUNS_RUNNING_D3D_FULL_SCREEN (a game)
#   4 = QUNS_PRESENTATION_MODE
#   5 = normal desktop
SCREEN_CLAIMED_STATES = frozenset({2, 3, 4})

FAKE_ENV = "JARVIS_FAKE_QUNS"


def notification_state() -> int:
    """The Windows fullscreen/presentation state. JARVIS_FAKE_QUNS overrides for
    deterministic tests. Any failure reads as 'normal desktop' (5)."""
    fake = os.environ.get(FAKE_ENV)
    if fake:
        try:
            return int(fake)
        except ValueError:
            return 5
    try:
        import ctypes
        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(
                ctypes.byref(state)) == 0:  # S_OK
            return state.value
    except Exception:
        pass
    return 5


def screen_is_claimed() -> bool:
    """True when a fullscreen app / game / presentation owns the screen.

    A scheduled routine that launches apps mid-race is worse than one that
    doesn't run: skipping is recoverable, interrupting is not.
    """
    return notification_state() in SCREEN_CLAIMED_STATES
