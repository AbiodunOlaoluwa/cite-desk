"""The two local models: an embedder (meaning-based search) and a cross-encoder (re-ranking).

Both run on CPU through ONNX (fastembed), so search needs no API key and no GPU. Tests swap in tiny deterministic
fakes with the same interface.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import numpy as np

EMBED_MODEL = os.environ.get("CITE_DESK_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
RERANK_MODEL = os.environ.get("CITE_DESK_RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
CACHE = os.environ.get("CITE_DESK_MODEL_CACHE", str(Path(__file__).resolve().parent.parent / "data" / "models"))
THREADS = int(os.environ.get("CITE_DESK_THREADS", "0")) or None
# ONNX Runtime keeps the memory of its largest batch. One passage at a time: ~400 MB instead of 1.6 GB, and faster,
# because short passages are no longer padded to the longest in the batch.
RERANK_BATCH = int(os.environ.get("CITE_DESK_RERANK_BATCH", "1"))
# bge models were trained with this instruction on the query side only.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder:
    name = EMBED_MODEL

    def __init__(self):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(EMBED_MODEL, cache_dir=CACHE, threads=THREADS)

    def passages(self, texts: list[str]) -> np.ndarray:
        return _normalise(np.array(list(self.model.embed(texts, batch_size=32)), dtype=np.float32))

    def query(self, text: str) -> np.ndarray:
        return _normalise(np.array(list(self.model.embed([QUERY_PREFIX + text])), dtype=np.float32))[0]


class Reranker:
    name = RERANK_MODEL

    def __init__(self):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.model = TextCrossEncoder(RERANK_MODEL, cache_dir=CACHE, threads=THREADS)

    def score(self, query: str, docs: list[str]) -> list[float]:
        return [float(s) for s in self.model.rerank(query, docs, batch_size=RERANK_BATCH)]


def _normalise(m: np.ndarray) -> np.ndarray:
    return m / np.clip(np.linalg.norm(m, axis=1, keepdims=True), 1e-9, None)


# ---- fakes for tests: same interface, no downloads ---------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class FakeEmbedder:
    """Hashed bag of words: enough to make 'similar words, similar vectors' true in tests."""

    name = "fake-hash-64"

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(64, dtype=np.float32)
        for t in _tokens(text):
            v[int(hashlib.md5(t[:5].encode()).hexdigest(), 16) % 64] += 1
        return v

    def passages(self, texts: list[str]) -> np.ndarray:
        return _normalise(np.array([self._vec(t) for t in texts]) + 1e-6)

    def query(self, text: str) -> np.ndarray:
        return self.passages([text])[0]


class FakeReranker:
    name = "fake-overlap"

    def score(self, query: str, docs: list[str]) -> list[float]:
        q = set(_tokens(query))
        return [len(q & set(_tokens(d))) - 3.0 for d in docs]
