"""Slice-2 primitive tests. Stage 2: capture + diff math. Stage 3 appends
launch_app / ui_tree coverage (incl. the hostile and mangled-name cases)."""
from __future__ import annotations

import numpy as np

from jarvis.primitives import screen


# ---------- stage 2: capture_screen + screenshot_diff ----------

def test_capture_screen_returns_fullscreen_array():
    img = screen.capture_screen()
    assert img.ndim == 3 and img.shape[2] == 3
    assert img.shape[0] > 100 and img.shape[1] > 100


def test_diff_identical_is_zero():
    a = np.zeros((200, 300, 3), dtype=np.uint8)
    assert screen.screenshot_diff(a, a.copy()) == 0.0


def test_diff_detects_synthetic_change():
    a = np.zeros((200, 300, 3), dtype=np.uint8)
    b = a.copy()
    b[:40, :, :] = 200  # top 20% of rows changed hard
    frac = screen.screenshot_diff(a, b)
    assert 0.1 < frac < 0.35, frac


def test_diff_below_pixel_threshold_ignored():
    """Sub-threshold noise (cursor blink antialiasing etc.) must not count."""
    a = np.full((200, 300, 3), 100, dtype=np.uint8)
    b = a + 5  # below default pixel threshold
    assert screen.screenshot_diff(a, b) == 0.0


def test_diff_shape_mismatch_reports_full_change():
    a = np.zeros((100, 100, 3), dtype=np.uint8)
    b = np.zeros((120, 100, 3), dtype=np.uint8)
    assert screen.screenshot_diff(a, b) == 1.0


def test_live_rapid_captures_mostly_static():
    a = screen.capture_screen()
    b = screen.capture_screen()
    frac = screen.screenshot_diff(a, b)
    assert frac < 0.5, f"back-to-back captures differ by {frac:.0%} — diff is broken"
