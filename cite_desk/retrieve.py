"""Hybrid retrieval: keyword (BM25) and meaning (embeddings) run side by side, are fused, then re-ranked.

    question ─┬─ BM25 over FTS5 ──────┐
              │                       ├─ reciprocal rank fusion ─ cross-encoder re-rank ─ top k ─ abstain?
              └─ embedding similarity ┘

Every stage is timed and recorded in a trace, so the app can show exactly why a passage came back.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

import numpy as np

from .index import Index

STOP = set("""a an and are as at be been being but by can could did do does doing for from had has have having how i if in
into is it its me my no not of on or our should so than that the their them then there these they this to under up us
was we were what when where which while who whom why will with would you your yours am any all also may must need
needs i'm i've""".split())

JURISDICTIONS = {
    "US": r"\b(?:US|U\.S\.|USA|United States|America|American|CAN-SPAM|TCPA|FTC|FCC|California|New York|Texas|Florida)\b",
    "EU": r"\b(?:EU|European Union|Europe|European|GDPR|ePrivacy|Germany|German|France|French|Spain|Spanish|Italy|Italian|"
          r"Netherlands|Dutch|Ireland|Irish|Belgium|Poland|Sweden|Austria|Portugal|Denmark|Finland)\b",
    "UK": r"\b(?:UK|U\.K\.|United Kingdom|Britain|British|England|Scotland|Wales|Northern Ireland|PECR|ICO)\b",
    "CA": r"\b(?:Canada|Canadian|CASL|Ontario|Quebec|Toronto|Vancouver|CRTC)\b",
    "NG": r"\b(?:Nigeria|Nigerian|NDPA|NDPC|GAID|NDP Act)\b",
}
RRF_K = 60
POOL = 40  # candidates taken from each retriever
RERANK_POOL = int(os.environ.get("CITE_DESK_RERANK_POOL", "24"))  # fused candidates the cross-encoder reads
RERANK_WORDS = int(os.environ.get("CITE_DESK_RERANK_WORDS", "300"))  # of each passage, after its header
PER_PROVISION = 2  # at most this many chunks from one provision in the final list


@dataclass
class Settings:
    mode: str = "rerank"  # bm25 | dense | hybrid | rerank
    k: int = 6
    jurisdiction_filter: bool = True
    refuse_below: float | None = None  # best re-rank score below this: refuse without answering
    weak_below: float | None = None  # below this: answer, but flag the match as weak


@dataclass
class Result:
    question: str
    hits: list[dict]
    trace: dict = field(default_factory=dict)
    abstain: bool = False
    weak: bool = False


def detect_jurisdictions(q: str) -> list[str]:
    return [j for j, pat in JURISDICTIONS.items() if re.search(pat, q)]


def fts_query(q: str) -> tuple[str, list[str]]:
    terms = [t for t in re.findall(r"[A-Za-z0-9]+", q.lower()) if t not in STOP and len(t) > 1]
    terms = list(dict.fromkeys(terms))
    return " OR ".join(f'"{t}"' for t in terms), terms


class Retriever:
    def __init__(self, index: Index, embedder, reranker):
        self.index, self.embedder, self.reranker = index, embedder, reranker

    def _bm25(self, q: str, allowed: set[str] | None) -> list[tuple[int, float]]:
        match, terms = fts_query(q)
        if not terms:
            return []
        sql = ("SELECT c.rowid, bm25(chunks_fts, 2.0, 1.0) AS s FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid "
               "WHERE chunks_fts MATCH ?")
        args: list = [match]
        if allowed:
            sql += f" AND c.jurisdiction IN ({','.join('?' * len(allowed))})"
            args += sorted(allowed)
        sql += " ORDER BY s LIMIT ?"
        args.append(POOL)
        return [(r[0], -float(r[1])) for r in self.index.conn.execute(sql, args)]  # FTS5 bm25: lower is better

    def _dense(self, q: str, allowed: set[str] | None) -> list[tuple[int, float]]:
        qv = self.embedder.query(q)
        sims = self.index.matrix @ qv
        mask = self.index.vector_rows(allowed)
        sims = np.where(mask, sims, -np.inf)
        top = np.argsort(-sims)[:POOL]
        return [(int(self.index.rowids[i]), float(sims[i])) for i in top if np.isfinite(sims[i])]

    def search(self, q: str, s: Settings | None = None) -> Result:
        s = s or Settings()
        t: dict[str, float] = {}
        trace: dict = {"settings": s.__dict__.copy()}
        found = detect_jurisdictions(q) if s.jurisdiction_filter else []
        allowed = set(found) or None
        if allowed and not (set(self.index.jurisdictions) & allowed):
            allowed = None  # nothing in this collection matches; don't filter everything away
        trace["query"] = {"terms": fts_query(q)[1], "jurisdictions": found, "filtered_to": sorted(allowed) if allowed else []}

        bm25, dense = [], []
        if s.mode in ("bm25", "hybrid", "rerank"):
            t0 = time.perf_counter()
            bm25 = self._bm25(q, allowed)
            t["bm25"] = time.perf_counter() - t0
        if s.mode in ("dense", "hybrid", "rerank"):
            t0 = time.perf_counter()
            dense = self._dense(q, allowed)
            t["dense"] = time.perf_counter() - t0

        if s.mode == "bm25":
            ranked = [(rid, sc) for rid, sc in bm25]
        elif s.mode == "dense":
            ranked = [(rid, sc) for rid, sc in dense]
        else:
            t0 = time.perf_counter()
            ranked = sorted(_fused_scores(bm25, dense).items(), key=lambda x: -x[1])
            t["fuse"] = time.perf_counter() - t0

        rerank_scores: dict[int, float] = {}
        if s.mode == "rerank" and ranked:
            t0 = time.perf_counter()
            pool = ranked[:RERANK_POOL]
            docs = [self._passage(rid) for rid, _ in pool]
            scores = self.reranker.score(q, docs)
            rerank_scores = {rid: sc for (rid, _), sc in zip(pool, scores)}
            ranked = sorted(rerank_scores.items(), key=lambda x: -x[1])
            t["rerank"] = time.perf_counter() - t0

        hits, per = [], {}
        for rid, score in ranked:
            c = self.index.by_rowid[rid]
            if per.get(c["provision"], 0) >= PER_PROVISION:
                continue
            per[c["provision"]] = per.get(c["provision"], 0) + 1
            hits.append({**c, "score": score})
            if len(hits) >= s.k:
                break

        bm25_rank = {rid: i for i, (rid, _) in enumerate(bm25, start=1)}
        dense_rank = {rid: i for i, (rid, _) in enumerate(dense, start=1)}
        for h in hits:
            h["ranks"] = {"bm25": bm25_rank.get(h["rowid"]), "dense": dense_rank.get(h["rowid"])}

        def brief(lst, n=10):
            return [{"id": self.index.by_rowid[rid]["id"], "cite": self.index.by_rowid[rid]["cite"],
                     "title": self.index.by_rowid[rid]["title"], "score": round(sc, 4)} for rid, sc in lst[:n]]

        trace["bm25"] = brief(bm25)
        trace["dense"] = brief(dense)
        if s.mode in ("hybrid", "rerank"):
            trace["fused"] = brief(sorted(((rid, sc) for rid, sc in _fused_scores(bm25, dense).items()), key=lambda x: -x[1]))
        if rerank_scores:
            trace["reranked"] = brief(sorted(rerank_scores.items(), key=lambda x: -x[1]))
        top = hits[0]["score"] if hits else None
        gated = s.mode == "rerank"
        abstain = gated and s.refuse_below is not None and (top is None or top < s.refuse_below)
        weak = gated and not abstain and s.weak_below is not None and top is not None and top < s.weak_below
        trace["abstain"] = {"top_score": top, "refuse_below": s.refuse_below, "weak_below": s.weak_below,
                            "abstain": abstain, "weak": weak}
        trace["ms"] = {k: round(v * 1000, 2) for k, v in t.items()}
        trace["rerank_pool"], trace["rerank_words"] = RERANK_POOL, RERANK_WORDS
        return Result(q, hits, trace, abstain, weak)

    def _passage(self, rowid: int) -> str:
        c = self.index.by_rowid[rowid]
        body = " ".join(c["text"].split()[:RERANK_WORDS])
        return f"{c['header']}\n{c['context']}\n{body}".strip()


def _fused_scores(bm25, dense) -> dict[int, float]:
    fused: dict[int, float] = {}
    for lst in (bm25, dense):
        for rank, (rid, _) in enumerate(lst, start=1):
            fused[rid] = fused.get(rid, 0.0) + 1.0 / (RRF_K + rank)
    return fused
