"""Windows DPAPI wrappers (slice 10) — encryption at rest for personal data.

CryptProtectData encrypts with the logged-in Windows user's credentials:
keyless (no passphrase, no key file to leak), decryptable only by the same
user on the same machine. That is the correct property for personal memory on
a single-user desktop — and it means a lost Windows profile makes the data
unreadable (intended).

Thin + mockable on purpose: memory.py degrades honestly when available()
is False (it refuses to persist rather than writing plaintext)."""
from __future__ import annotations

_DESCRIPTION = "jarvis-memory"


def available() -> bool:
    try:
        import win32crypt  # noqa: F401
        return True
    except Exception:
        return False


def protect(data: bytes) -> bytes:
    """Encrypt bytes with DPAPI. Raises if win32crypt is unavailable."""
    import win32crypt
    return win32crypt.CryptProtectData(data, _DESCRIPTION, None, None, None, 0)


def unprotect(blob: bytes) -> bytes:
    """Decrypt a DPAPI blob. Raises on a bad/foreign blob."""
    import win32crypt
    _desc, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return data
