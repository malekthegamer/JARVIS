"""Local sentence embeddings (slice 19) — the semantic half of memory retrieval.

all-MiniLM-L6-v2 (384-dim) driven DIRECTLY by libraries this machine already
has (onnxruntime + tokenizers + numpy) — no torch, no fastembed, no network
at runtime, no API key. Memory text NEVER leaves the machine (the slice-10
privacy posture; an API embedder would ship stored personal facts off-box on
every message).

One-time setup (the email-OAuth pattern — runtime never downloads):
    python -m jarvis.core.embedder --setup

No model on disk -> available() is False and memory retrieval degrades
honestly to the slice-10 lexical path. The model files are public weights
(not secrets) and live plaintext at data/models/minilm/ (gitignored); the
derived per-record vectors live inside the DPAPI-encrypted memory blob.

Measured on this machine (Stage-0 probe): load 153 ms once, ~3 ms per query;
related pairs cos 0.39-0.53 vs unrelated 0.01-0.03.
"""
from __future__ import annotations

import threading
from pathlib import Path

from jarvis import config

MODEL_DIR = config.DATA_DIR / "models" / "minilm"
MODEL_FILE = MODEL_DIR / "model.onnx"
TOKENIZER_FILE = MODEL_DIR / "tokenizer.json"
MODEL_TAG = "minilm-l6-v2"   # stamped into records; mismatch -> re-embed
MAX_TOKENS = 256

_HF_BASE = ("https://huggingface.co/sentence-transformers/"
            "all-MiniLM-L6-v2/resolve/main")
_DOWNLOADS = {MODEL_FILE: f"{_HF_BASE}/onnx/model.onnx",
              TOKENIZER_FILE: f"{_HF_BASE}/tokenizer.json"}

_lock = threading.Lock()
_session = None
_tokenizer = None


def available() -> bool:
    """Model files present AND the runtime imports — mirrors dpapi.available().
    Callers degrade (lexical retrieval) when False; nothing here raises."""
    try:
        if not (MODEL_FILE.exists() and TOKENIZER_FILE.exists()):
            return False
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
        return True
    except Exception:
        return False


def _ensure_loaded():
    """Lazy, once, under the lock (wake.py's deferred-heavy-import pattern)."""
    global _session, _tokenizer
    if _session is not None:
        return
    import onnxruntime as ort
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file(str(TOKENIZER_FILE))
    tok.enable_padding()
    tok.enable_truncation(max_length=MAX_TOKENS)
    _tokenizer = tok
    _session = ort.InferenceSession(str(MODEL_FILE),
                                    providers=["CPUExecutionProvider"])


def embed(texts: list[str]) -> list[list[float]]:
    """L2-normalized mean-pooled sentence vectors (so cosine == dot).
    Raises on failure — callers (memory.retrieve/add) catch and degrade."""
    import numpy as np
    with _lock:
        _ensure_loaded()
        enc = _tokenizer.encode_batch([str(t) for t in texts])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        (hidden,) = _session.run(
            ["last_hidden_state"],
            {"input_ids": ids, "attention_mask": mask,
             "token_type_ids": np.zeros_like(ids)})
    m = mask[..., None].astype(np.float32)
    pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
    pooled /= np.linalg.norm(pooled, axis=1, keepdims=True)
    return [[float(x) for x in row] for row in pooled]


def _setup() -> int:
    """Download the model files (one-time, ~90 MB). Honest failure messages;
    partial downloads are cleaned up so available() can't half-trigger."""
    import urllib.error
    import urllib.request
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for target, url in _DOWNLOADS.items():
        if target.exists():
            print(f"already present: {target.name}")
            continue
        print(f"downloading {target.name} ...")
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            urllib.request.urlretrieve(url, tmp)
            tmp.rename(target)
        except urllib.error.URLError as exc:
            tmp.unlink(missing_ok=True)
            print(f"FAILED to download {url}: {exc}\n"
                  f"Memory retrieval will keep using the lexical fallback "
                  f"until this succeeds.")
            return 1
        print(f"  -> {target} ({target.stat().st_size:,} bytes)")
    if available():
        vecs = embed(["the cat sat on the mat", "a feline rested on the rug"])
        cos = sum(a * b for a, b in zip(vecs[0], vecs[1]))
        print(f"self-check: cosine(similar pair) = {cos:.3f} "
              f"({'OK' if cos > 0.5 else 'UNEXPECTEDLY LOW'})")
        return 0
    print("setup finished but available() is still False — check the files above.")
    return 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Local embedding model setup.")
    ap.add_argument("--setup", action="store_true",
                    help="download the MiniLM model files (one-time)")
    ns = ap.parse_args()
    if ns.setup:
        raise SystemExit(_setup())
    print(f"available: {available()}  ({MODEL_DIR})")
