"""List all audio input devices, flagging likely REAL microphones.

Run any time audio devices change:  python tools/list_mics.py
Voicemeeter/VB-Audio/etc. virtual devices are marked as such; the device
index can shift when hardware is re-plugged, so re-run rather than hardcode.
To pin a device: set "stt.mic_device_index" in data/settings.json (or the
dashboard settings page).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voice.capture import find_real_mic, is_probably_real_mic, list_input_devices  # noqa: E402


def main() -> None:
    devices = list_input_devices()
    if not devices:
        print("No input devices found (is PyAudio installed?)")
        return
    print(f"{'idx':>4}  {'type':<12} name")
    print("-" * 70)
    for idx, name in devices:
        tag = "REAL MIC ✓" if is_probably_real_mic(name) else "virtual/out"
        print(f"{idx:>4}  {tag:<12} {name}")
    picked_idx, picked_name = find_real_mic()
    print("-" * 70)
    print(f"Auto-detected mic: index={picked_idx}  ({picked_name})")


if __name__ == "__main__":
    main()
