"""Slice-10 tests. STAGE 1 = the encrypted, durable store: real encryption at
rest (DPAPI), cross-instance persistence, honest degradation on a missing /
corrupt store, and forget-never-guesses ambiguity handling."""
from __future__ import annotations

import pytest

from jarvis.core import dpapi
from jarvis.core.memory import MemoryStore


@pytest.fixture()
def store_path(tmp_path):
    return tmp_path / "mem" / "memories.bin"


def test_store_encrypted_at_rest_not_plaintext(store_path):
    s = MemoryStore(store_path)
    s.add("I take my coffee black, no sugar")
    raw = store_path.read_bytes()
    assert b"coffee" not in raw and b"black" not in raw, \
        "memory text must be encrypted at rest, never plaintext on disk"


def test_memory_read_by_fresh_instance(store_path):
    MemoryStore(store_path).add("my dentist is Dr. Alvarez")
    fresh = MemoryStore(store_path)  # simulates a restart
    texts = [r["text"] for r in fresh.all()]
    assert any("Alvarez" in t for t in texts)


def test_missing_store_starts_empty(store_path):
    assert not store_path.exists()
    assert MemoryStore(store_path).all() == []


def test_corrupt_store_starts_empty_no_crash(store_path):
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_bytes(b"\x00\x01 not a valid dpapi blob \xff\xfe")
    s = MemoryStore(store_path)          # must NOT raise
    assert s.all() == []


def test_add_all_delete(store_path):
    s = MemoryStore(store_path)
    s.add("I am allergic to peanuts")
    s.add("my flight is on Tuesday")
    assert len(s.all()) == 2
    res = s.delete("peanuts")
    assert res["status"] == "deleted"
    assert "peanuts" in res["removed"]["text"]
    assert len(s.all()) == 1


def test_forget_ambiguous_lists_candidates_deletes_nothing(store_path):
    """The review condition: 'forget X' matching >1 memory must delete NOTHING
    and list candidates — never silently guess the wrong one."""
    s = MemoryStore(store_path)
    s.add("I take my coffee black")
    s.add("I drink my coffee at 8am")
    res = s.delete("coffee")
    assert res["status"] == "ambiguous"
    assert len(res["candidates"]) == 2
    assert len(s.all()) == 2, "an ambiguous forget must delete nothing"


def test_forget_no_match_deletes_nothing(store_path):
    s = MemoryStore(store_path)
    s.add("I am allergic to peanuts")
    res = s.delete("motorcycle")
    assert res["status"] == "none"
    assert len(s.all()) == 1


def test_save_refuses_when_dpapi_unavailable_no_plaintext(store_path, monkeypatch):
    """If encryption is unavailable, the store REFUSES to persist — it never
    falls back to writing plaintext personal data."""
    monkeypatch.setattr(dpapi, "available", lambda: False)
    monkeypatch.setattr(dpapi, "protect",
                        lambda b: (_ for _ in ()).throw(RuntimeError("no dpapi")))
    s = MemoryStore(store_path)
    with pytest.raises(Exception):
        s.add("I take my coffee black")
    # nothing (especially no plaintext) landed on disk
    if store_path.exists():
        assert b"black" not in store_path.read_bytes()


def test_clear_removes_all(store_path):
    s = MemoryStore(store_path)
    s.add("fact one")
    s.add("fact two")
    s.clear()
    assert s.all() == []
    assert MemoryStore(store_path).all() == []


# ---------- STAGE 2: relevance-gated retrieval + prompt formatting ----------

def test_retrieve_unrelated_query_returns_nothing(store_path):
    """THE anti-pollution property: an unrelated message surfaces NO memory."""
    s = MemoryStore(store_path)
    s.add("I am allergic to peanuts")
    s.add("my sister's birthday is in March")
    assert s.retrieve("what's the weather in Tokyo today?") == []


def test_retrieve_relevant_query_returns_memory(store_path):
    s = MemoryStore(store_path)
    s.add("I am allergic to peanuts")
    s.add("my flight is on Tuesday")
    hits = s.retrieve("what am I allergic to?")
    assert len(hits) == 1 and "peanuts" in hits[0]["text"]


def test_retrieve_caps_top_k(store_path):
    s = MemoryStore(store_path)
    for i in range(10):
        s.add(f"I like coffee variety number {i}")
    hits = s.retrieve("tell me about my coffee preferences", k=3)
    assert len(hits) == 3


def test_stopwords_ignored(store_path):
    """A query overlapping only on stopwords must NOT match."""
    s = MemoryStore(store_path)
    s.add("I am allergic to peanuts")
    # shares only 'i','am','to' (stopwords) with the memory -> no content overlap
    assert s.retrieve("I am going to the shop") == []


def test_format_empty_is_blank(store_path):
    s = MemoryStore(store_path)
    assert s.format_for_prompt([]) == ""


def test_format_carries_no_volunteer_framing(store_path):
    s = MemoryStore(store_path)
    rec = s.add("I am allergic to peanuts")
    block = s.format_for_prompt([rec])
    assert "peanuts" in block
    assert "volunteer" in block.lower()  # the don't-volunteer instruction is present


# ================================================================ Slice 19:
# semantic retrieval (fake-embedder seam — deterministic, no model needed)
# and pinned preferences. The REAL model's quality is measured by
# tests/harness_memory_eval.py, not asserted here.

def _norm(v):
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v]


@pytest.fixture()
def fake_embedder(monkeypatch):
    """Deterministic vectors: coffee-ish texts cluster, others are orthogonal.
    Patches the module-attribute seam memory.py reads at call time."""
    from jarvis.core import embedder

    VECS = {
        "I take my coffee black, no sugar":            _norm([1.0, 0.05, 0.0]),
        "how do I like my hot drinks in the morning?": _norm([0.95, 0.1, 0.0]),
        "I am allergic to peanuts":                    _norm([0.0, 1.0, 0.0]),
        "what's the weather in Tokyo today?":          _norm([0.0, 0.0, 1.0]),
    }
    calls = []

    def fake_embed(texts):
        calls.append(list(texts))
        # unknown text -> a vector far from everything (still normalized)
        return [VECS.get(t, _norm([0.31, -0.4, 0.86])) for t in texts]

    monkeypatch.setattr(embedder, "available", lambda: True)
    monkeypatch.setattr(embedder, "embed", fake_embed)
    monkeypatch.setattr(embedder, "MODEL_TAG", "fake-test-model")
    fake_embed.calls = calls
    return fake_embed


def test_semantic_retrieve_paraphrase_hits(store_path, fake_embedder):
    s = MemoryStore(store_path)
    s.add("I take my coffee black, no sugar")
    s.add("I am allergic to peanuts")
    out = s.retrieve("how do I like my hot drinks in the morning?")
    assert len(out) == 1 and "coffee" in out[0]["text"], out


def test_semantic_threshold_gates_low_similarity(store_path, fake_embedder):
    from jarvis.core.settings_store import settings
    s = MemoryStore(store_path)
    s.add("I am allergic to peanuts")     # orthogonal to the coffee query
    prior = settings.get("memory.semantic_threshold", 0.35)
    settings.set("memory.semantic_threshold", 0.9, persist=False)
    try:
        out = s.retrieve("how do I like my hot drinks in the morning?")
    finally:
        settings.set("memory.semantic_threshold", prior, persist=False)
    assert out == [], out


def test_semantic_threshold_fallback_matches_settings_default():
    """memory.py's inline get() fallback must equal DEFAULT_SETTINGS.

    Slice 34: these had drifted (0.30 in memory.py vs 0.35 in settings_store).
    Dead code while the key exists, but a settings.json wipe would silently
    retrieve at a threshold nobody tuned. Pin them together.
    """
    import inspect
    import re

    from jarvis.core import memory as jmemory
    from jarvis.core.settings_store import DEFAULT_SETTINGS

    src = inspect.getsource(jmemory.MemoryStore.retrieve)
    found = re.search(r'settings\.get\(\s*"memory\.semantic_threshold"\s*,\s*'
                      r'([0-9.]+)\s*\)', src)
    assert found, "semantic_threshold fallback not found in retrieve()"
    assert float(found.group(1)) == DEFAULT_SETTINGS["memory"]["semantic_threshold"]


def test_hybrid_keeps_lexical_keyword_hit(store_path, fake_embedder):
    """A record whose fake cosine is LOW but which shares a real content
    token must still surface — keyword recall can never regress."""
    s = MemoryStore(store_path)
    s.add("I am allergic to peanuts")
    out = s.retrieve("am I allergic to peanuts?")  # unknown query vec -> low cos
    assert len(out) == 1 and "peanuts" in out[0]["text"], out


def test_semantic_unrelated_query_returns_nothing(store_path, fake_embedder):
    s = MemoryStore(store_path)
    s.add("I take my coffee black, no sugar")
    s.add("I am allergic to peanuts")
    assert s.retrieve("what's the weather in Tokyo today?") == []


def test_fallback_when_embedder_unavailable(store_path, monkeypatch):
    from jarvis.core import embedder
    monkeypatch.setattr(embedder, "available", lambda: False)
    monkeypatch.setattr(embedder, "embed",
                        lambda texts: pytest.fail("embed must not be called"))
    s = MemoryStore(store_path)
    s.add("I am allergic to peanuts")
    s.add("my sister's birthday is in March")
    # Exactly slice-10 behavior: keyword hit works, unrelated query is empty.
    hits = s.retrieve("what am I allergic to?")
    assert len(hits) == 1 and "peanuts" in hits[0]["text"]
    assert s.retrieve("what's the weather in Tokyo today?") == []


def test_lazy_backfill_embeds_legacy_records_once(store_path, monkeypatch, fake_embedder):
    from jarvis.core import embedder
    # Write a record while the embedder is unavailable -> no vec stored.
    monkeypatch.setattr(embedder, "available", lambda: False)
    s = MemoryStore(store_path)
    rec = s.add("I take my coffee black, no sugar")
    assert "vec" not in s.all()[0]
    # Embedder comes back (fake_embedder patched available -> re-patch True).
    monkeypatch.setattr(embedder, "available", lambda: True)
    fake_embedder.calls.clear()
    out = s.retrieve("how do I like my hot drinks in the morning?")
    assert out and out[0]["id"] == rec["id"]
    embedded_texts = [t for call in fake_embedder.calls for t in call]
    assert "I take my coffee black, no sugar" in embedded_texts  # backfilled
    # Second retrieve: only the query is embedded, never the records again.
    fake_embedder.calls.clear()
    s.retrieve("how do I like my hot drinks in the morning?")
    embedded_texts = [t for call in fake_embedder.calls for t in call]
    assert embedded_texts == ["how do I like my hot drinks in the morning?"]
    # And the backfilled vec survives a restart (persisted once).
    fresh = MemoryStore(store_path)
    assert fresh.all()[0].get("vec"), "backfilled vector must be persisted"


# ---------------- pinned always-on preferences (slice 19 Stage 3) ----------

def test_add_pinned_stored(store_path):
    s = MemoryStore(store_path)
    rec = s.add("always address me as Captain", pinned=True)
    assert rec.get("pinned") is True
    assert MemoryStore(store_path).all()[0].get("pinned") is True  # survives restart
    assert s.pinned() and s.pinned()[0]["id"] == rec["id"]


def test_forget_removes_pinned(store_path):
    s = MemoryStore(store_path)
    s.add("always address me as Captain", pinned=True)
    res = s.delete("captain")
    assert res["status"] == "deleted"
    assert s.pinned() == [] and s.all() == []


def test_legacy_records_without_pinned_load_fine(store_path):
    import json as _json
    legacy = [{"id": "old00001", "text": "I am allergic to peanuts",
               "kind": "fact", "created_at": "2026-01-01T00:00:00+00:00"}]
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_bytes(dpapi.protect(_json.dumps(legacy).encode("utf-8")))
    s = MemoryStore(store_path)
    assert len(s.all()) == 1 and s.pinned() == []
    hits = s.retrieve("am I allergic to peanuts?")   # works without vec/pinned
    assert hits and hits[0]["id"] == "old00001"


def test_pinned_excluded_from_relevance_topk(store_path):
    s = MemoryStore(store_path)
    s.add("I am allergic to peanuts", pinned=True)
    s.add("peanut butter goes in the top cupboard")
    hits = s.retrieve("am I allergic to peanuts?")
    assert all(not r.get("pinned") for r in hits), \
        "pinned records are always-on via their own block, never top-k competitors"


def test_format_pinned_block_framing_and_cap(store_path):
    from jarvis.core.settings_store import settings
    s = MemoryStore(store_path)
    for i in range(12):
        s.add(f"standing preference number {i}", pinned=True)
    block = s.format_pinned_for_prompt(s.pinned())
    assert "STANDING PREFERENCES" in block
    cap = int(settings.get("memory.pinned_max", 10))
    assert block.count("standing preference number") == cap  # newest capped
    assert "number 11" in block and "number 0" not in block
    assert s.format_pinned_for_prompt([]) == ""
