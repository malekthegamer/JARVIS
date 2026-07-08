"""Screen perception: capture (mss) + change detection for the verify step.

capture_screen() is internal-only this slice — screenshots feed
screenshot_diff, not the model (vision round-trip is a later slice).
"""
from __future__ import annotations

import numpy as np

# Per-channel delta a pixel must exceed to count as "changed" — absorbs
# cursor blink antialiasing and JPEG-ish shimmer without masking real change.
PIXEL_THRESHOLD = 12
# Compare every Nth pixel: ~16x faster and plenty for "did the screen change".
DOWNSAMPLE_STEP = 4


def capture_screen(monitor: int = 0) -> np.ndarray:
    """Grab the full virtual desktop (monitor 0 spans all displays).
    Returns an (H, W, 3) BGR uint8 array."""
    import mss  # lazy

    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[monitor])
    return np.asarray(shot)[:, :, :3].copy()


def screenshot_diff(before: np.ndarray, after: np.ndarray) -> float:
    """Fraction of (downsampled) pixels that meaningfully changed, 0.0-1.0.
    Mismatched shapes (resolution change) count as everything changed."""
    if before.shape != after.shape:
        return 1.0
    a = before[::DOWNSAMPLE_STEP, ::DOWNSAMPLE_STEP].astype(np.int16)
    b = after[::DOWNSAMPLE_STEP, ::DOWNSAMPLE_STEP].astype(np.int16)
    changed = (np.abs(a - b) > PIXEL_THRESHOLD).any(axis=2)
    return float(changed.mean())
