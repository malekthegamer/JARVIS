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
