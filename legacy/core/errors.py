"""Single error type every provider maps its SDK exceptions onto.

The never-crash contract: run loops catch ProviderError (and bare Exception as
a last resort) and render `friendly()` — an exception never kills the process.
"""
from __future__ import annotations


class ProviderError(Exception):
    """A provider failure with a classified kind for clear user messaging."""

    KINDS = {"missing_key", "rate_limit", "connection", "bad_response", "quota_exceeded", "generic"}

    def __init__(self, kind: str, provider: str, message: str = ""):
        self.kind = kind if kind in self.KINDS else "generic"
        self.provider = provider
        self.message = message
        super().__init__(f"[{provider}:{self.kind}] {message}")

    def friendly(self) -> str:
        base = {
            "missing_key": f"{self.provider} isn't configured — add its API key in Settings.",
            "rate_limit": f"{self.provider} is rate-limiting us. Give it a moment and try again.",
            "connection": f"Couldn't reach {self.provider} — check your connection (or that the local server is running).",
            "quota_exceeded": f"{self.provider} quota is used up. Switch providers in Settings or top up.",
            "bad_response": f"{self.provider} returned something unexpected.",
            "generic": f"{self.provider} hit a problem.",
        }[self.kind]
        return f"{base}" + (f" ({self.message})" if self.message and self.kind in ("bad_response", "generic") else "")


def classify_exception(exc: Exception, provider: str) -> ProviderError:
    """Best-effort mapping of an arbitrary SDK exception to a ProviderError."""
    if isinstance(exc, ProviderError):
        return exc
    text = f"{type(exc).__name__}: {exc}"
    lower = text.lower()
    if any(s in lower for s in ("api key", "api_key", "unauthorized", "401", "permission", "invalid key", "authentication")):
        kind = "missing_key"
    elif any(s in lower for s in ("rate limit", "429", "resource_exhausted", "too many requests")):
        kind = "rate_limit"
    elif any(s in lower for s in ("quota", "insufficient credits", "payment", "billing")):
        kind = "quota_exceeded"
    elif any(s in lower for s in ("connect", "timeout", "timed out", "unreachable", "refused", "dns", "network", "getaddrinfo")):
        kind = "connection"
    else:
        kind = "generic"
    return ProviderError(kind, provider, text[:300])
