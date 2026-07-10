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
