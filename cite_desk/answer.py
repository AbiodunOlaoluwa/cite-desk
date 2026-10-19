"""Turn retrieved passages into an answer that cites them, or a refusal that says what is missing.

Two modes:
- **Claude** (CITE_DESK_MODE=anthropic and an API key): each retrieved chunk is passed as a `search_result` whose
  content blocks are the individual paragraphs, so a citation points at "PECR reg. 22(3)(a)", not a whole page.
  An answer that cites nothing is not shown as an answer.
- **Extractive** (no key): the cross-encoder scores every paragraph of the top passages against the question and
  the best few are quoted, each with its citation. Nothing is paraphrased, so nothing can be invented.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, dataclass, field

from .chunk import fmt_ref
from .corpus import manifest

MODEL = os.environ.get("CITE_DESK_MODEL", "claude-opus-5")
PRICES = {"claude-opus-5": (5, 25), "claude-opus-5-5": (4, 20), "claude-sonnet-5": (2, 10), "claude-haiku-4-5": (1, 5)}

SYSTEM = """You answer questions about {title} ({blurb}) using only the search results in the user's message.

Rules:
1. Every statement of what the law or guidance says must be supported by a citation to a search result.
2. If the search results do not answer the question, say so plainly in one or two sentences and say what is missing
   (for example, that law or country is not in these documents). Do not fill gaps from general knowledge.
3. Name the law and the place for each point, e.g. "Under the UK's PECR...". If rules from several places apply,
   keep them apart.
4. Statutes and regulations are the law; guidance (like the FTC's guide) explains it. Take the rule from the law and
   practical detail from guidance.
5. Plain English, short: two to five sentences or a short list. No disclaimers; the app shows one."""


@dataclass
class Citation:
    n: int
    chunk_id: str
    cite: str
    title: str
    url: str
    quote: str
    jurisdiction: str


@dataclass
class Answer:
    mode: str  # claude | extractive
    status: str  # answered | refused
    segments: list[dict] = field(default_factory=list)  # [{"text": "...", "cites": [1, 2]}]
    citations: list[Citation] = field(default_factory=list)
    reason: str = ""
    model: str = ""
    usage: dict = field(default_factory=dict)
    ms: float = 0.0
    sent: list[dict] = field(default_factory=list)  # what the model was shown, for the inspector

    def to_json(self) -> dict:
        d = asdict(self)
        d["citations"] = [asdict(c) for c in self.citations]
        return d


def mode() -> str:
    return "claude" if os.environ.get("CITE_DESK_MODE") == "anthropic" and os.environ.get("ANTHROPIC_API_KEY") else "extractive"


def coverage(collection: str) -> str:
    docs = manifest()["collections"][collection]["docs"]
    by: dict[str, list[str]] = {}
    for d in docs:
        by.setdefault(d["jurisdiction"], []).append(d["short"])
    names = {"US": "US", "EU": "EU", "UK": "UK", "CA": "Canada", "NG": "Nigeria", "NG-LA": "Lagos"}
    return "; ".join(f"{names.get(j, j)}: {', '.join(v)}" for j, v in by.items())


def refusal(collection: str, trace: dict, why: str) -> Answer:
    filtered = trace.get("query", {}).get("filtered_to") or []
    where = f" I searched only the {', '.join(filtered)} documents because the question names {'it' if len(filtered) == 1 else 'them'}." if filtered else ""
    text = (f"These documents don't answer that.{where} This collection covers {coverage(collection)}.")
    return Answer(mode=mode(), status="refused", segments=[{"text": text, "cites": []}], reason=why)


def _units(hit: dict) -> list[tuple[str, str]]:
    """The citable paragraphs of a chunk, as (ref, text). The carried-over lead-in comes first, as context."""
    units = []
    if hit.get("context"):
        units.append(("", f"[Lead-in from earlier in this provision] {hit['context']}"))
    units += [(b["ref"], b["text"]) for b in hit["blocks"]]
    return units


def _cite_label(hit: dict, refs: list[str]) -> str:
    refs = [r for r in refs if r]
    if not refs:
        return hit["label"]
    first, last = fmt_ref(refs[0]), fmt_ref(refs[-1])
    return f"{hit['label']}{first}" if first == last else f"{hit['label']}{first}–{last}"


def answer_claude(question: str, hits: list[dict], collection: str, client=None) -> Answer:
    import anthropic

    col = manifest()["collections"][collection]
    client = client or anthropic.Anthropic()
    results, unit_index = [], []
    for h in hits:
        units = _units(h)
        unit_index.append(units)
        results.append({"type": "search_result", "source": h["url"], "title": f"{h['cite']}: {h['title']}",
                        "content": [{"type": "text", "text": t} for _, t in units], "citations": {"enabled": True}})
    t0 = time.perf_counter()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=1500,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=[{"type": "text", "text": SYSTEM.format(title=col["title"], blurb=col["blurb"]), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": [*results, {"type": "text", "text": question}]}],
        output_config={"effort": "low"},
    )
    ms = (time.perf_counter() - t0) * 1000
    usage = usage_of(response.usage, MODEL)
    sent = [{"title": r["title"], "source": r["source"], "blocks": [c["text"] for c in r["content"]]} for r in results]
    if response.stop_reason == "refusal":
        a = refusal(collection, {}, "the model declined")
        a.usage, a.ms, a.model, a.sent = usage, ms, MODEL, sent
        return a

    segments, citations, seen = [], [], {}
    for block in response.content:
        if block.type != "text":
            continue
        nums = []
        for c in getattr(block, "citations", None) or []:
            if getattr(c, "type", "") != "search_result_location":
                continue
            i = c.search_result_index
            if i >= len(hits):
                continue
            key = (i, c.start_block_index, c.end_block_index)
            if key not in seen:
                units = unit_index[i][c.start_block_index:c.end_block_index]
                h = hits[i]
                seen[key] = len(citations) + 1
                citations.append(Citation(seen[key], h["id"], _cite_label(h, [r for r, _ in units]), h["title"], h["url"],
                                          c.cited_text.strip(), h["jurisdiction"]))
            nums.append(seen[key])
        segments.append({"text": block.text, "cites": sorted(set(nums))})
    text = "".join(s["text"] for s in segments).strip()
    if not citations:
        # An answer that cites nothing could be from the model's memory, not these documents: show it as a refusal.
        a = Answer(mode="claude", status="refused", segments=[{"text": text or "No answer.", "cites": []}],
                   reason="the model's reply cited none of the passages", model=MODEL, usage=usage, ms=ms, sent=sent)
        return a
    return Answer(mode="claude", status="answered", segments=segments, citations=citations, model=MODEL, usage=usage, ms=ms, sent=sent)


def answer_extractive(question: str, hits: list[dict], reranker, per_answer: int = 3, weak: bool = False) -> Answer:
    t0 = time.perf_counter()
    cands = []
    for h in hits[:3]:
        for ref, text in _units(h):
            if ref == "" and text.startswith("[Lead-in"):
                continue
            if len(text.split()) < 6:
                continue
            cands.append((h, ref, text))
    scores = reranker.score(question, [t for _, _, t in cands]) if cands else []
    ranked = sorted(zip(cands, scores), key=lambda x: -x[1])[:per_answer]
    ranked.sort(key=lambda x: (hits.index(x[0][0]), x[0][0]["blocks"].index(next(b for b in x[0][0]["blocks"] if b["ref"] == x[0][1] and b["text"] == x[0][2]))))
    intro = ("These are the closest passages, but they match the question only weakly, so they may not answer it. "
             "Quoted exactly:" if weak else
             "Running without a language model, so here are the passages that answer it most closely, quoted exactly:")
    citations, segments = [], [{"text": intro, "cites": []}]
    for n, ((h, ref, text), _) in enumerate(ranked, start=1):
        text = _with_children(h, ref, text)
        citations.append(Citation(n, h["id"], _cite_label(h, [ref]), h["title"], h["url"], text, h["jurisdiction"]))
        segments.append({"text": text, "cites": [n], "quote": True})
    return Answer(mode="extractive", status="weak" if weak else "answered", segments=segments, citations=citations,
                  reason="the best passage scored below the weak-match line" if weak else "",
                  ms=(time.perf_counter() - t0) * 1000)


def _with_children(hit: dict, ref: str, text: str, limit: int = 110) -> str:
    """A lead-in like '... unless the message provides—' says nothing alone: quote the list items under it too."""
    if not ref or not text.rstrip().endswith(("—", "-", ":", "where", "following", "unless", "must", "shall")):
        return text
    parts = [text]
    for b in hit["blocks"]:
        if b["ref"] != ref and b["ref"].startswith(ref) and len(" ".join(parts).split()) < limit:
            parts.append(b["text"])
    return " ".join(parts)


def usage_of(u, model: str) -> dict:
    pin, pout = PRICES.get(model, PRICES["claude-opus-5"])
    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
    return {"input_tokens": u.input_tokens + cache_read + cache_write, "output_tokens": u.output_tokens,
            "cache_read_tokens": cache_read,
            "cost_micros": round(u.input_tokens * pin + cache_write * pin * 1.25 + cache_read * pin * 0.1 + u.output_tokens * pout)}


def plain(answer: Answer) -> str:
    """For the command line: the answer with [n] markers and the sources under it."""
    out = ""
    for s in answer.segments:
        marks = "".join(f"[{n}]" for n in s["cites"])
        out += (f"\n  > {s['text']} {marks}\n" if s.get("quote") else f"{s['text']}{marks}")
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    if answer.citations:
        out += "\n\n" + "\n".join(f"[{c.n}] {c.cite}: {c.title}" for c in answer.citations)
    return out
