"""Long-term, cross-session memory (slice 10, spec §1.5).

Durable facts the user EXPLICITLY asked JARVIS to remember — never inferred,
never conversation transcripts. Stored DPAPI-encrypted at rest; retrieval is
relevance-gated so an unrelated conversation surfaces nothing (which is also
the structural defense against resurfacing sensitive content unprompted).

Every read path degrades honestly: a missing / corrupt / foreign-encrypted
store loads as empty and never crashes. The write path REFUSES rather than
fall back to plaintext when encryption is unavailable.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from jarvis import config
from jarvis.core import dpapi

DEFAULT_PATH = config.DATA_DIR / "memory" / "memories.bin"

# Function words only — content tokens are what relevance + forget-matching use.
_STOPWORDS = frozenset("""
a an the this that these those it its i me my mine you your yours we our us
he she they them his her their is are was were be been being am do does did
to of in on at for with as by from and or but not no if so then than
what whats how when where why which who whom that s re ll ve
please can could would should will just about my
""".split())


def _tokens(text: str) -> list[str]:
    """Lowercased content tokens (stopwords + 1-char noise dropped)."""
    raw = "".join(c if c.isalnum() else " " for c in str(text or "").lower()).split()
    return [t for t in raw if len(t) > 1 and t not in _STOPWORDS]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._records: list[dict] = self._load()

    # ---------- persistence (honest degradation) ----------
    def _load(self) -> list[dict]:
        try:
            if not self.path.exists():
                return []
            data = json.loads(dpapi.unprotect(self.path.read_bytes()).decode("utf-8"))
            return [r for r in data if isinstance(r, dict) and r.get("text")] \
                if isinstance(data, list) else []
        except Exception:
            # missing / corrupt / foreign-encrypted / not-JSON -> start empty
            return []

    def _persist(self) -> None:
        if not dpapi.available():
            raise RuntimeError("secure storage is unavailable — refusing to "
                               "write personal data in plaintext")
        blob = dpapi.protect(json.dumps(self._records).encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(blob)

    # ---------- writes ----------
    def add(self, text: str, kind: str = "fact") -> dict:
        text = " ".join(str(text or "").split())
        if not text:
            raise ValueError("empty memory")
        rec = {"id": uuid.uuid4().hex[:8], "text": text, "kind": kind,
               "created_at": _now()}
        with self._lock:
            self._records.append(rec)
            try:
                self._persist()
            except Exception:
                self._records.pop()   # roll back — no partial/plaintext state
                raise
        return dict(rec)

    def delete(self, query: str) -> dict:
        """Never guesses. -> {status: deleted|none|ambiguous, ...}."""
        with self._lock:
            matches = self._match(query)
            if not matches:
                return {"status": "none"}
            if len(matches) > 1:
                return {"status": "ambiguous",
                        "candidates": [dict(m) for m in matches]}
            rec = matches[0]
            snapshot = list(self._records)
            self._records = [r for r in self._records if r["id"] != rec["id"]]
            try:
                self._persist()
            except Exception:
                self._records = snapshot
                raise
            return {"status": "deleted", "removed": dict(rec)}

    def clear(self) -> int:
        with self._lock:
            n = len(self._records)
            snapshot = list(self._records)
            self._records = []
            try:
                self._persist()
            except Exception:
                self._records = snapshot
                raise
            return n

    # ---------- reads ----------
    def all(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._records]

    def _match(self, query: str) -> list[dict]:
        q = set(_tokens(query))
        if not q:
            return []
        with self._lock:
            return [r for r in self._records if q & set(_tokens(r["text"]))]

    def retrieve(self, query: str, k: int | None = None,
                 threshold: int | None = None) -> list[dict]:
        """The memories relevant to THIS message — and nothing when nothing is
        relevant (that empty result is the anti-pollution property and the
        structural defense against surfacing sensitive facts unprompted).

        Scored by shared content-token count; ties broken by recency."""
        from jarvis.core.settings_store import settings
        if k is None:
            k = int(settings.get("memory.retrieve_k", 5))
        if threshold is None:
            threshold = int(settings.get("memory.relevance_threshold", 1))
        q = set(_tokens(query))
        if not q:
            return []
        scored = []
        with self._lock:
            for r in self._records:
                score = len(q & set(_tokens(r["text"])))
                if score >= threshold:
                    scored.append((score, r))
        scored.sort(key=lambda sr: (sr[0], sr[1]["created_at"]), reverse=True)
        return [dict(r) for _score, r in scored[:max(0, k)]]

    def format_for_prompt(self, records: list[dict]) -> str:
        """A system-prompt block — empty when there are no records, so the
        prompt is untouched for unrelated messages. Frames the facts as
        use-if-relevant context, never something to volunteer."""
        if not records:
            return ""
        lines = "\n".join(f"- {r['text']}" for r in records)
        return ("\n\n--- WHAT YOU REMEMBER ABOUT THE USER ---\n"
                "The user previously asked you to remember these facts. Use them "
                "ONLY if they are relevant to the user's current message. Do NOT "
                "volunteer or recite stored personal facts unprompted, and do not "
                "bring up a sensitive fact in a context the user didn't raise.\n"
                f"{lines}\n--- END MEMORY ---")


# One process-wide store (the brain injects/overrides this in tests).
memory_store = MemoryStore()
