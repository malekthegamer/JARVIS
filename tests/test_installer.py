"""Slice 37: static pins on install.bat — the one-time setup a friend runs.

These parse the script; they never execute it (it downloads ~500 MB and mutates
the machine). The doctrine is the same as the README pin added in slice 36: a
shipped script that references files which do not exist is a falsehood the test
suite should catch, not the user.
"""
from __future__ import annotations

import re

from jarvis import config

INSTALLER = config.BASE_DIR / "install.bat"


def _text() -> str:
    return INSTALLER.read_text(encoding="utf-8", errors="replace")


def test_install_bat_exists_and_is_batch():
    assert INSTALLER.exists(), "install.bat is the documented entry point"
    assert "@echo" in _text().lower(), "does not look like a batch script"


def test_install_bat_references_only_existing_repo_files():
    """Every repo-relative file the installer names must actually exist."""
    text = _text()
    # NOTE: `pyw` must precede `py` in the alternation, else tray_start.pyw is
    # captured as the non-existent "tray_start.py".
    for ref in set(re.findall(r"(?<![\w.\\/-])(\w[\w-]*\.(?:pyw|py|txt))\b", text)):
        if ref.startswith("pywin32_postinstall"):
            continue                      # lives inside the venv, not the repo
        assert (config.BASE_DIR / ref).exists(), \
            f"install.bat references missing repo file: {ref}"


def test_install_bat_targets_venv_pythonw_for_the_shortcut():
    """The shortcut must point at the venv's pythonw.exe (no console window,
    no 'activate' step) and pass tray_start.pyw."""
    text = _text().lower()
    assert "pythonw.exe" in text, "shortcut should use pythonw (no console)"
    assert "tray_start.pyw" in text
    assert ".venv" in text


def test_install_bat_installs_chromium_only():
    """Guards a measured 494 MB regression: `playwright install` with no
    browser argument pulls firefox + webkit, which JARVIS never uses."""
    text = _text()
    assert "playwright install chromium" in text, \
        "must request chromium explicitly"
    assert not re.search(r"playwright install(?!\s+chromium)", text), \
        "bare `playwright install` would fetch firefox+webkit too"


def test_install_bat_has_crlf_line_endings():
    """cmd.exe mishandles an LF-only .bat — the real failure was
    "'install.bat' is not recognized", i.e. the script silently did nothing.
    Found by RUNNING the installer, not by reading it. Also enforced for fresh
    clones by .gitattributes (`*.bat text eol=crlf`)."""
    raw = INSTALLER.read_bytes()
    assert raw.count(b"\n") - raw.count(b"\r\n") == 0, \
        "install.bat contains bare LF line endings; cmd.exe needs CRLF"


def test_gitattributes_pins_bat_eol():
    ga = config.BASE_DIR / ".gitattributes"
    assert ga.exists(), ".gitattributes must survive to protect .bat endings"
    assert "*.bat text eol=crlf" in ga.read_text(encoding="utf-8")


def test_install_bat_downloads_wake_word_model():
    """openwakeword ships WITHOUT its .onnx models; `pip install` alone leaves
    the 'hey jarvis' wake-word toggle silently unable to turn on (v1.0.3 bug).
    The installer must fetch them."""
    text = _text()
    assert "openwakeword" in text and "download_models" in text, \
        "install.bat must download the openwakeword models"


def test_install_bat_registers_pywin32_com():
    """pywin32's COM registration is NOT automatic inside a venv, and win32com
    powers DPAPI encryption, the Recycle Bin and shortcuts. Missing this makes
    JARVIS fail at runtime in ways that look unrelated."""
    assert "pywin32_postinstall" in _text()


# ---------- the Python 3.12 contract (install-time AND run-time) ----------
#
# Python 3.13 REMOVED the stdlib `audioop` and `aifc` modules (PEP 594).
# SpeechRecognition imports both unguarded at module top level, and
# voice/wake.py imports audioop directly for mic resampling. pip installs
# cleanly on 3.13 (PyAudio ships a cp313 wheel), so the installer would print
# "Done." and EVERY voice path would then fail at first use — on a
# voice-driven agent. Anyone who installed Python recently has 3.13+, and the
# old `py -3` fallback selected the NEWEST interpreter.

def test_install_bat_requires_python_312_not_any_python3():
    """The killer detail: a bare `py -3` fallback picks the newest Python."""
    text = _text()
    assert "py -3.12" in text, "must look for 3.12 explicitly"
    assert not re.search(r"py -3\s+--version", text), \
        "a bare `py -3` fallback selects the NEWEST Python (3.13+), where all voice breaks"
    assert "version_info" in text, \
        "a bare `python` fallback must be version-checked, not trusted"


def test_install_bat_installs_312_when_missing():
    """winget must pin 3.12 — not Python.Python.3, which resolves to latest."""
    text = _text()
    assert "Python.Python.3.12" in text
    assert not re.search(r"Python\.Python\.3(?!\.12)", text)


def test_install_bat_records_why_312_is_required():
    """A future maintainer must not 'helpfully' relax this back to any 3.x."""
    low = _text().lower()
    assert "audioop" in low and "3.13" in low, \
        "the script must state WHY 3.12 is pinned, or it will get relaxed"


def test_voice_guard_explains_unsupported_python_instead_of_module_not_found():
    """Belt-and-braces for anyone who bypasses install.bat: a raw
    `ModuleNotFoundError: No module named 'audioop'` is undiagnosable. The
    guard must name the real cause and the fix."""
    from jarvis.voice import capture
    msg = capture.unsupported_python_message("audioop")
    assert "audioop" in msg
    assert "3.12" in msg and "3.13" in msg
    assert "install.bat" in msg.lower()


def test_voice_guard_passes_through_on_supported_python():
    """On 3.12 the guard must be a no-op — it must never block a working
    install (it runs on the per-frame wake-word path)."""
    import sys

    from jarvis.voice import capture
    if sys.version_info[:2] == (3, 12):
        capture.require_audio_stdlib()   # must not raise
