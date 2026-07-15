"""Slice 19 — THE memory-retrieval metric (golden set with known answers).

Not pytest-collected (harness_ prefix): the semantic/hybrid modes need the
downloaded MiniLM model (python -m jarvis.core.embedder --setup). Run it
instead of trusting any recorded numbers:

    python tests/harness_memory_eval.py --mode lexical    # baseline
    python tests/harness_memory_eval.py --mode hybrid     # the shipped path
    python tests/harness_memory_eval.py --mode semantic   # diagnostic (no lexical guard)

Metrics (HARNESS §6b: always report the COST beside the win):
  paraphrase recall@k   — the gap this slice targets (zero-token-overlap queries)
  keyword recall@k      — must never regress vs lexical
  distractor top-1      — right sibling surfaces first
  FALSE-SURFACE rate    — negatives that injected ANYTHING (the privacy cost)
  median latency        — per retrieve() call

THE GOLDEN SET IS FROZEN. Tuning memory.semantic_threshold against these
numbers is system tuning; editing the set after seeing results is benchmark
gaming — don't. Paraphrase queries are mechanically validated to share ZERO
content tokens with their target (via memory._tokens); the harness refuses
to run if that invariant breaks.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.core import memory as jmemory
from jarvis.core.memory import MemoryStore, _tokens
from jarvis.core.settings_store import settings

# ----------------------------------------------------------------- golden set
# 25 memories; sibling pairs (cat/dog, dentist/doctor, sister/mother,
# flight/train) exist to make distractor probes honest.
MEMORIES = [
    "I take my coffee black, no sugar",             # 0
    "I am allergic to peanuts",                     # 1
    "My cat is called Whiskers",                    # 2
    "My dog is called Rex",                         # 3
    "My dentist is Dr. Alvarez",                    # 4
    "My doctor is Dr. Chen",                        # 5
    "My wifi password is hunter2-blue",             # 6
    "My car is a silver Honda Civic",               # 7
    "My sister's birthday is March 12th",           # 8
    "My mother's birthday is June 3rd",             # 9
    "I prefer metric units",                        # 10
    "I like my steak medium rare",                  # 11
    "My favorite color is forest green",            # 12
    "I work night shifts on weekends",              # 13
    "My gym locker code is 4417",                   # 14
    "I want replies to be short and direct",        # 15
    "My flight to Berlin leaves on Tuesday",        # 16
    "My train to Paris leaves on Friday",           # 17
    "The spare key is under the third flowerpot",   # 18
    "My glasses prescription is -2.5 both eyes",    # 19
    "I am vegetarian",                              # 20
    "My boss is named Sandra",                      # 21
    "My apartment is number 7B",                    # 22
    "I play tennis on Thursday evenings",           # 23
    "My blood type is O negative",                  # 24
]

# (query, expected_memory_index) — zero content-token overlap with the target,
# validated mechanically below.
PARAPHRASE = [
    ("how do I like my hot drinks in the morning?", 0),
    ("which foods could make me sick?", 1),
    ("what's my pet feline's name?", 2),
    ("what do we call our puppy?", 3),
    ("who fixes my teeth?", 4),
    ("who do I see when I feel unwell?", 5),
    ("what do I type to get online at home?", 6),
    ("what vehicle do I drive?", 7),
    ("when was my sibling born?", 8),
    ("when should I send mom a card?", 9),
    ("do I use imperial measurements?", 10),
    ("how should my beef be cooked?", 11),
    ("which shade do I love most?", 12),
    ("when am I working late?", 13),
    ("what's the combination at the fitness center?", 14),
    ("how verbose should your answers be?", 15),
    ("when do I fly to Germany?", 16),
    ("when does my rail trip to France depart?", 17),
    ("how can someone get into the house if I'm locked out?", 18),
    ("what strength are my spectacles?", 19),
    ("do I eat meat?", 20),
    ("who do I report to at the office?", 21),
]

KEYWORD = [
    ("am I allergic to peanuts?", 1),
    ("what's my wifi password?", 6),
    ("tell me about my Honda Civic", 7),
    ("when is my sister's birthday?", 8),
    ("do I prefer metric units?", 10),
    ("what's my favorite color?", 12),
    ("what's my gym locker code?", 14),
    ("when does my flight to Berlin leave?", 16),
    ("where is the spare key?", 18),
    ("what is my blood type?", 24),
]

NEGATIVE = [
    "what's the weather in Tokyo today?",
    "who won the champions league final?",
    "set a timer for ten minutes",
    "open notepad and write a note",
    "what is the capital of Australia?",
    "how tall is Mount Everest?",
    "tell me a joke",
    "search the web for the latest python release",
    "turn the volume down to twenty",
    "close every browser tab except youtube",
    "how many moons does Jupiter have?",
    "write a haiku about autumn",
    "what's 15 percent of 80?",
    "turn on do not disturb",
    "what year did the Berlin wall fall?",
]

# (query, expected_index) — the RIGHT sibling must come back top-1.
DISTRACTOR = [
    ("what's my cat's name?", 2),
    ("what's my dog's name?", 3),
    ("who is my dentist?", 4),
    ("who is my doctor?", 5),
    ("which day is my sister's birthday?", 8),
    ("which day is my mother's birthday?", 9),
    ("when does the flight leave?", 16),
    ("when does the train leave?", 17),
    ("what's the plan for getting to Berlin?", 16),
    ("remind me about my Paris trip", 17),
]


def _validate_paraphrase_invariant() -> None:
    for query, idx in PARAPHRASE:
        overlap = set(_tokens(query)) & set(_tokens(MEMORIES[idx]))
        if overlap:
            sys.exit(f"GOLDEN SET INVALID: paraphrase query '{query}' shares "
                     f"content tokens {overlap} with its target — fix the set "
                     f"BEFORE any results exist, never after.")


def _force_mode(mode: str) -> None:
    """lexical: embedder reported unavailable (the honest-fallback path).
    semantic: lexical guard disabled (diagnostic). hybrid: shipped path."""
    try:
        from jarvis.core import embedder
    except ImportError:
        if mode != "lexical":
            sys.exit(f"mode '{mode}' needs jarvis.core.embedder (Stage 2); "
                     f"only --mode lexical runs against the current code.")
        return
    if mode == "lexical":
        embedder.available = lambda: False  # process-local; harness only
    elif mode == "semantic":
        jmemory.LEXICAL_GUARD = False       # diagnostic knob (Stage 2)
    if mode != "lexical" and not embedder.available():
        sys.exit("embedder model missing — run: python -m jarvis.core.embedder --setup")


def run(mode: str, k: int) -> None:
    _validate_paraphrase_invariant()
    _force_mode(mode)
    tmp = Path(tempfile.mkdtemp(prefix="jarvis-memeval-")) / "memories.bin"
    store = MemoryStore(tmp)
    ids = [store.add(text)["id"] for text in MEMORIES]

    latencies: list[float] = []

    def retrieve(q: str):
        t0 = time.perf_counter()
        out = store.retrieve(q, k=k)
        latencies.append((time.perf_counter() - t0) * 1000)
        return out

    para_hits = sum(1 for q, i in PARAPHRASE
                    if any(r["id"] == ids[i] for r in retrieve(q)))
    kw_hits = sum(1 for q, i in KEYWORD
                  if any(r["id"] == ids[i] for r in retrieve(q)))
    dis_hits = 0
    for q, i in DISTRACTOR:
        res = retrieve(q)
        dis_hits += bool(res and res[0]["id"] == ids[i])
    false_surface = sum(1 for q in NEGATIVE if retrieve(q))

    thr = settings.get("memory.semantic_threshold", None)
    print(f"\n=== memory retrieval eval — mode={mode} k={k} "
          f"semantic_threshold={thr} ===")
    print(f"paraphrase recall@{k} : {para_hits}/{len(PARAPHRASE)} "
          f"= {para_hits/len(PARAPHRASE):.3f}   <- the targeted gap")
    print(f"keyword    recall@{k} : {kw_hits}/{len(KEYWORD)} "
          f"= {kw_hits/len(KEYWORD):.3f}   <- must not regress")
    print(f"distractor top-1     : {dis_hits}/{len(DISTRACTOR)} "
          f"= {dis_hits/len(DISTRACTOR):.3f}")
    print(f"FALSE-SURFACE rate   : {false_surface}/{len(NEGATIVE)} "
          f"= {false_surface/len(NEGATIVE):.3f}   <- the privacy COST metric")
    print(f"median retrieve()    : {statistics.median(latencies):.1f} ms "
          f"({len(latencies)} calls)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["lexical", "semantic", "hybrid"],
                    default="hybrid")
    ap.add_argument("--k", type=int,
                    default=int(settings.get("memory.retrieve_k", 5)))
    ns = ap.parse_args()
    run(ns.mode, ns.k)
