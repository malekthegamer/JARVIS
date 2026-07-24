"""Autostart launcher (slice 23): the HKCU Run key points pythonw.exe here.
A Run-key command starts in system32 with no package path, so put the repo
root on sys.path before importing the package, then start the tray app."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis.tray import run_guarded  # noqa: E402

# run_guarded (not main) so a startup crash under pythonw.exe — which has no
# console — is written to data/tray_error.log and shown in a dialog instead of
# failing silently.
run_guarded()
