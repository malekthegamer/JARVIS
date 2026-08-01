"""One definition of the Gemini model fallback order, shared by every caller.

Slice 44 built the chain for `brain.think()`. Slice 51 needed the same order for
vision's screen-Q&A path, and duplicating the six lines would fork the config
contract — `brain.fallback_models` could quietly mean two different things
depending on which file read it.

This module depends on `settings` and NOTHING else, which is the point: vision
can import it without importing `brain` (brain imports `primitives`, so the
reverse edge would be a cycle).
"""
from __future__ import annotations

from jarvis.core.settings_store import settings

# Failure kinds worth retrying on a SIBLING model. Everything else — a bad key,
# a malformed request — would fail identically on every model in the chain, so
# retrying would only burn quota and bury the real error.
TRANSIENT_KINDS = ("rate_limit", "quota_exceeded", "connection")


def model_chain() -> list[str]:
    """Active model first, then the configured fallbacks — deduped, order kept.

    MEASURED (slice 44, stage 0): the primary caps at ~15 RPM, and
    gemini-2.5-flash ANSWERED while the primary was 429 — sibling models have
    SEPARATE quota buckets, which is the only reason a model-level chain helps
    at all. Dedupe matters for the same reason: retrying the model that just
    429'd re-checks a bucket already known to be dry.
    """
    active = settings.get("brain.models.gemini", "gemini-3.1-flash-lite")
    chain = [active]
    for extra in (settings.get("brain.fallback_models", []) or []):
        if extra and extra not in chain:
            chain.append(extra)
    return chain
