"""Autostart launcher (slice 23): the HKCU Run key points pythonw.exe here.
A Run-key command starts in system32 with no package path, so put the repo
root on sys.path before importing the package, then start the tray app."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis.tray import main  # noqa: E402

main()
