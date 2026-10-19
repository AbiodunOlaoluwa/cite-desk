"""Measure retrieval against hand-labelled questions, variant by variant, and calibrate when to refuse.

Each question in evals/<collection>.jsonl lists the provisions that answer it ("gold"), optionally narrowed to a
paragraph ("us-tcpa-rules/64.1200#(c)(1)"), or none at all for questions the documents cannot answer.
Questions are split dev/test: the refusal threshold is chosen on dev and reported on test, so the headline numbers
are not tuned on the questions they are measured on.
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from .retrieve import Retriever, Settings

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
VARIANTS = [
    ("bm25", "Keyword only (BM25)", Settings(mode="bm25", jurisdiction_filter=False)),
    ("dense", "Meaning only (embeddings)", Settings(mode="dense", jurisdiction_filter=False)),
    ("hybrid", "Hybrid (BM25 + embeddings, fused)", Settings(mode="hybrid", jurisdiction_filter=False)),
    ("rerank", "Hybrid + re-rank", Settings(mode="rerank", jurisdiction_filter=False)),
    ("rerank_filter", "Hybrid + re-rank + jurisdiction filter", Settings(mode="rerank", jurisdiction_filter=True)),
]
KS = (1, 3, 5)


def questions(collection: str) -> list[dict]:
    path = EVALS / f"{collection}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def matches(hit: dict, gold: str) -> bool:
    pid, _, ref = gold.partition("#")
    if hit["provision"] != pid:
        return False
    return not ref or any(r.startswith(ref) for r in hit["refs"])


def first_relevant(hits: list[dict], gold: list[str]) -> int | None:
    for i, h in enumerate(hits, start=1):
        if any(matches(h, g) for g in gold):
            return i
    return None


def _auc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def _best_threshold(scored: list[tuple[float, bool]]) -> float:
    """The cut that best separates answerable from unanswerable (balanced accuracy), on dev questions only."""
    cands = sorted({s for s, _ in scored})
    best, best_t = -1.0, cands[0] if cands else 0.0
    for i, t in enumerate(cands):
        cut = t if i == 0 else (cands[i - 1] + t) / 2
        tp = sum(1 for s, a in scored if a and s >= cut)
        tn = sum(1 for s, a in scored if not a and s < cut)
        pos = sum(1 for _, a in scored if a) or 1
        neg = sum(1 for _, a in scored if not a) or 1
        bal = (tp / pos + tn / neg) / 2
        if bal > best:
            best, best_t = bal, cut
    return round(best_t, 3)


def _floor(scored: list[tuple[float, bool]], keep: float = 0.95) -> float:
    """The highest cut that still lets `keep` of the answerable (dev) questions through: refuse only the clear misses."""
    pos = sorted(s for s, a in scored if a)
    if not pos:
        return float("-inf")
    allowed_out = int(len(pos) * (1 - keep))
    return round(pos[allowed_out] - 0.01, 3)


def _rates(scored: list[tuple[float, bool]], cut: float) -> tuple[float, float]:
    pos = [s for s, a in scored if a]
    neg = [s for s, a in scored if not a]
    return (round(sum(1 for s in pos if s >= cut) / (len(pos) or 1), 3), round(sum(1 for s in neg if s < cut) / (len(neg) or 1), 3))


def run(collection: str, retriever: Retriever, progress=None) -> dict:
    qs = questions(collection)
    answerable = [q for q in qs if q["answerable"]]
    total = len(VARIANTS) * len(qs)
    done = 0
    out = {"collection": collection, "questions": len(qs), "answerable": len(answerable),
           "unanswerable": len(qs) - len(answerable), "variants": [], "per_question": {}}
    for key, label, settings in VARIANTS:
        ranks, lat, top_scores = [], [], []
        for q in qs:
            t0 = time.perf_counter()
            res = retriever.search(q["q"], Settings(**{**settings.__dict__, "k": 10}))
            lat.append((time.perf_counter() - t0) * 1000)
            done += 1
            if progress:
                progress(key, done, total)
            top = res.hits[0]["score"] if res.hits else float("-inf")
            top_scores.append((q, top))
            if q["answerable"]:
                r = first_relevant(res.hits, q["gold"])
                ranks.append(r)
                out["per_question"].setdefault(q["id"], {"q": q["q"], "gold": q["gold"], "split": q["split"]})[key] = {
                    "rank": r, "top": [h["cite"] for h in res.hits[:3]]}
        n = len(ranks) or 1
        row = {"key": key, "label": label,
               **{f"recall@{k}": round(sum(1 for r in ranks if r and r <= k) / n, 3) for k in KS},
               "mrr@10": round(sum(1 / r for r in ranks if r) / n, 3),
               "missed": sum(1 for r in ranks if not r),
               "p50_ms": round(statistics.median(lat), 1), "p95_ms": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 1)}
        if key.startswith("rerank"):
            pos = [s for q, s in top_scores if q["answerable"]]
            neg = [s for q, s in top_scores if not q["answerable"]]
            row["refusal_auc"] = None if _auc(pos, neg) is None else round(_auc(pos, neg), 3)
            dev = [(s, q["answerable"]) for q, s in top_scores if q["split"] == "dev"]
            test = [(s, q["answerable"]) for q, s in top_scores if q["split"] == "test"]
            weak, floor = _best_threshold(dev), _floor(dev)
            kept, refused = _rates(test, floor)
            strong, flagged = _rates(test, weak)
            row["refusal"] = {"refuse_below": floor, "weak_below": weak,
                              "test_kept_answerable": kept, "test_refused_unanswerable": refused,
                              "test_strong_answerable": strong, "test_flagged_unanswerable": flagged}
        out["variants"].append(row)
    best = out["variants"][-1].get("refusal", {})
    out["thresholds"] = {"refuse_below": best.get("refuse_below"), "weak_below": best.get("weak_below")}
    out["ran_at"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    return out


def save(results: list[dict], embed_name: str, rerank_name: str) -> Path:
    EVALS.mkdir(exist_ok=True)
    (EVALS / "results.json").write_text(json.dumps({"embed_model": embed_name, "rerank_model": rerank_name,
                                                    "collections": results}, indent=2) + "\n")
    thresholds = {r["collection"]: r["thresholds"] for r in results}
    (EVALS / "thresholds.json").write_text(json.dumps(thresholds, indent=2) + "\n")
    lines = ["# Retrieval evals", "",
             f"Embeddings: `{embed_name}`. Re-ranker: `{rerank_name}`. Run: {results[0]['ran_at']}.", "",
             "Recall@k: share of answerable questions with a correct provision in the top k. MRR: average of 1/rank of "
             "the first correct one. A question about a long section counts only if the chunk holds the right paragraph.", ""]
    for r in results:
        lines += [f"## {r['collection']} ({r['answerable']} answerable, {r['unanswerable']} unanswerable)", "",
                  "| Variant | Recall@1 | Recall@3 | Recall@5 | MRR@10 | Missed (top 10) | p50 ms | p95 ms |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for v in r["variants"]:
            lines.append(f"| {v['label']} | {v['recall@1']:.0%} | {v['recall@3']:.0%} | {v['recall@5']:.0%} | {v['mrr@10']:.2f} | "
                         f"{v['missed']} | {v['p50_ms']} | {v['p95_ms']} |")
        v = r["variants"][-1]
        if v.get("refusal"):
            ref = v["refusal"]
            lines += ["", f"**Refusing when the documents don't cover it.** The signal is the best re-rank score (AUC {v['refusal_auc']}: "
                          "it separates answerable from unanswerable questions only partly, because a question about a law that "
                          "is not here still finds closely related text). Both lines are chosen on dev and measured on test:", "",
                      f"- Refuse below {ref['refuse_below']}: keeps {ref['test_kept_answerable']:.0%} of answerable questions, "
                      f"refuses {ref['test_refused_unanswerable']:.0%} of unanswerable ones before any model is called.",
                      f"- Flag as a weak match below {ref['weak_below']}: {ref['test_strong_answerable']:.0%} of answerable questions "
                      f"clear it, {ref['test_flagged_unanswerable']:.0%} of unanswerable ones are flagged.",
                      "- With Claude switched on, the model reads the passages and refuses when they don't cover the question; "
                      "`python -m cite_desk eval --answers` measures that (needs an API key).", ""]
        misses = [(qid, pq) for qid, pq in r["per_question"].items() if pq.get("rerank_filter", {}).get("rank") != 1]
        if misses:
            lines += ["**Where the full pipeline's first result was not a correct provision:**", ""]
            for qid, pq in misses:
                got = pq["rerank_filter"]
                lines.append(f"- `{qid}` {pq['q']} Correct at rank {got['rank'] or 'none in top 10'}; "
                             f"first result {got['top'][0] if got['top'] else '-'}.")
            lines.append("")
    path = EVALS / "results.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def run_answers(collection: str, engine, client=None, progress=None) -> dict:
    """End to end with Claude: does it answer the answerable questions from the right provisions, and refuse the rest?

    - answered: share of answerable questions that got a cited answer (not a refusal)
    - citation precision: share of citations that point at a labelled provision
    - grounded hit: share of answered questions with at least one citation to a labelled provision
    - refused: share of unanswerable questions refused (by the gate or by the model)
    """
    qs = questions(collection)
    rows, cost, ms = [], 0, []
    for i, q in enumerate(qs, start=1):
        s = engine.search(collection, q["q"])
        a = engine.answer(collection, q["q"], s, client=client)
        cost += a.get("usage", {}).get("cost_micros", 0)
        ms.append(a.get("ms", 0))
        cited = [c for c in a["citations"]]
        by_id = engine.retriever(collection).index.by_id
        good = [c for c in cited if any(matches(by_id[c["chunk_id"]], g) for g in q["gold"])]
        rows.append({"id": q["id"], "answerable": q["answerable"], "status": a["status"], "citations": len(cited), "good": len(good),
                     "refused_by": "gate" if s["abstain"] else ("model" if a["status"] == "refused" else None)})
        if progress:
            progress("answers", i, len(qs))
    ans = [r for r in rows if r["answerable"]]
    una = [r for r in rows if not r["answerable"]]
    answered = [r for r in ans if r["status"] != "refused"]
    total_cites = sum(r["citations"] for r in answered) or 1
    return {
        "collection": collection,
        "answered": round(len(answered) / (len(ans) or 1), 3),
        "citation_precision": round(sum(r["good"] for r in answered) / total_cites, 3),
        "grounded_hit": round(sum(1 for r in answered if r["good"]) / (len(answered) or 1), 3),
        "refused_unanswerable": round(sum(1 for r in una if r["status"] == "refused") / (len(una) or 1), 3),
        "refused_by_gate": sum(1 for r in una if r["refused_by"] == "gate"),
        "refused_by_model": sum(1 for r in una if r["refused_by"] == "model"),
        "cost_usd": round(cost / 1e6, 4),
        "p50_answer_ms": round(statistics.median(ms), 0) if ms else 0,
        "rows": rows,
    }
