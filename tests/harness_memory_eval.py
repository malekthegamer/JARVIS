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

--- Slice 34: what has ALREADY been measured and ruled out (don't redo it) ---
Run with --verbose for the per-query cosines behind all of this.
  * Lowering semantic_threshold: DEAD. The 4 remaining paraphrase misses score
    0.169-0.280, but 3 UNRELATED negatives score 0.292-0.453 — the negatives
    OUTRANK the misses, so no threshold separates them. 0.35->0.30 buys zero
    recall and doubles false-surface; ->0.22 buys +3 recall and triples it.
  * Widening retrieve_k: DEAD. All 4 misses are below-threshold, 0 k-truncated.
  * Stemming the lexical guard: DEAD. None of the 4 pairs share a
    morphological root (unwell/doctor, type-online/wifi-password,
    verbose/replies, house-locked/spare-key) — there is no variant to recover.
  * A stronger embedding model: DEAD, probed head-to-head on this set at the
    false-surface<=0.067 bar — bge-small-en-v1.5 0.773 (mean) / 0.727 (cls +
    query-instruction) / 0.682 (cls), gte-small never even reaches the bar
    (its cosines bunch near 0.9). Shipped MiniLM-L6-v2 = 0.818, and those
    rival numbers are OPTIMISTIC (computed ignoring top-k truncation).
The residual ~18% is a small-embedding-model discrimination limit, not a
tuning gap. Reopen only with a materially better retrieval model (or a
rerank stage), and re-measure with this harness before believing it.
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


def _cos(qvec, rec) -> float:
    vec = rec.get("vec")
    return sum(a * b for a, b in zip(qvec, vec)) if vec else -1.0


def _lex_hit(qtok: set, rec: dict, lex_thr: int) -> bool:
    return bool(jmemory.LEXICAL_GUARD and qtok
                and len(qtok & set(_tokens(rec["text"]))) >= lex_thr)


def _diagnose(store: MemoryStore, ids: list[str], k: int) -> None:
    """Slice 34 Stage 0 — WHY each paraphrase miss missed.

    Additive diagnostic ONLY: it re-derives cosines outside retrieve() and
    prints them. It does not touch the golden set or the scoring logic that
    produced the numbers above. Reaching into store._records is deliberate,
    the same 'harness pokes internals for a diagnostic' style as the
    LEXICAL_GUARD knob in _force_mode.

    A miss is one of two structurally different things, and the fix differs:
      below-threshold : cosine never cleared semantic_threshold  -> threshold/
                        embedding lever
      k-truncated     : it qualified but lost its top-k slot     -> retrieve_k
                        lever
    """
    try:
        from jarvis.core import embedder
        if not embedder.available():
            print("\n(verbose diagnostic needs the embedder — skipped in this mode)")
            return
    except ImportError:
        print("\n(verbose diagnostic needs jarvis.core.embedder — skipped)")
        return

    sem_thr = float(settings.get("memory.semantic_threshold", 0.30))
    lex_thr = int(settings.get("memory.relevance_threshold", 1))
    recs = store._records  # intentional diagnostic reach-in (see docstring)

    print(f"\n--- per-paraphrase diagnostic (semantic_threshold={sem_thr}) ---")
    print(f"{'query':<50}{'cos':>7}{'margin':>8}{'rank':>6}  verdict")
    below = trunc = 0
    for q, idx in PARAPHRASE:
        qvec = embedder.embed([q])[0]
        qtok = set(_tokens(q))
        target = next(r for r in recs if r["id"] == ids[idx])
        c = _cos(qvec, target)
        qualified = (c >= sem_thr) or _lex_hit(qtok, target, lex_thr)
        # how many OTHER records outrank it among those that also qualify
        better = sum(1 for r in recs
                     if not r.get("pinned") and r["id"] != target["id"]
                     and _cos(qvec, r) > c
                     and (_cos(qvec, r) >= sem_thr or _lex_hit(qtok, r, lex_thr)))
        hit = any(r["id"] == ids[idx] for r in store.retrieve(q, k=k))
        if hit:
            verdict = "hit"
        elif qualified:
            verdict = "MISS  k-truncated"
            trunc += 1
        else:
            verdict = "MISS  below-threshold"
            below += 1
        print(f"{q[:50]:<50}{c:>7.3f}{c - sem_thr:>+8.3f}{better + 1:>6}  {verdict}")
    print(f"  -> {below} below-threshold, {trunc} k-truncated")

    print("\n--- per-negative diagnostic (how close the privacy cost runs) ---")
    print(f"{'negative query':<50}{'maxcos':>8}{'margin':>8}  surfaced?")
    for q in NEGATIVE:
        qvec = embedder.embed([q])[0]
        qtok = set(_tokens(q))
        live = [r for r in recs if not r.get("pinned")]
        best = max(_cos(qvec, r) for r in live)
        got = store.retrieve(q, k=k)
        if got:
            drivers = []
            for r in got:
                full = next(x for x in recs if x["id"] == r["id"])
                # BOTH gates can fire — showing only one would misattribute
                # which lever could actually remove this surface.
                why = ((["sem"] if _cos(qvec, full) >= sem_thr else [])
                       + (["lex"] if _lex_hit(qtok, full, lex_thr) else []))
                drivers.append("+".join(why) or "?")
            mark = f"YES [{','.join(drivers)}] {got[0]['text'][:30]!r}"
        else:
            mark = "no"
        print(f"{q[:50]:<50}{best:>8.3f}{best - sem_thr:>+8.3f}  {mark}")

    # The money table: what a threshold retune would BUY and COST, together.
    print("\n--- threshold sweep (win beside cost, HARNESS 6b) ---")
    print(f"{'thr':>6}{'paraphrase':>12}{'keyword':>10}{'distr top1':>12}"
          f"{'FALSE-SURFACE':>15}")
    original = settings.get("memory.semantic_threshold", None)
    try:
        for cand in (0.15, 0.20, 0.22, 0.25, 0.27, 0.30, 0.32, 0.35, 0.40, 0.45):
            settings.set("memory.semantic_threshold", cand, persist=False)
            p = sum(1 for q, i in PARAPHRASE
                    if any(r["id"] == ids[i] for r in store.retrieve(q, k=k)))
            kw = sum(1 for q, i in KEYWORD
                     if any(r["id"] == ids[i] for r in store.retrieve(q, k=k)))
            d = sum(1 for q, i in DISTRACTOR
                    if (lambda res: bool(res and res[0]["id"] == ids[i]))(
                        store.retrieve(q, k=k)))
            fs = sum(1 for q in NEGATIVE if store.retrieve(q, k=k))
            flag = "  <- shipped" if abs(cand - float(original or -1)) < 1e-9 else ""
            print(f"{cand:>6.2f}{p / len(PARAPHRASE):>12.3f}"
                  f"{kw / len(KEYWORD):>10.3f}{d / len(DISTRACTOR):>12.3f}"
                  f"{fs / len(NEGATIVE):>15.3f}{flag}")
    finally:
        settings.set("memory.semantic_threshold", original, persist=False)


def run(mode: str, k: int, verbose: bool = False) -> None:
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

    if verbose:
        _diagnose(store, ids, k)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["lexical", "semantic", "hybrid"],
                    default="hybrid")
    ap.add_argument("--k", type=int,
                    default=int(settings.get("memory.retrieve_k", 5)))
    ap.add_argument("--verbose", action="store_true",
                    help="per-query cosines, miss reasons + a threshold sweep")
    ns = ap.parse_args()
    run(ns.mode, ns.k, ns.verbose)
