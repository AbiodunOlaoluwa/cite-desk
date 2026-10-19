"""Structure-aware chunking: provisions are split only at their own paragraph boundaries.

A chunk never cuts a paragraph in half and never mixes two provisions. Each chunk carries a context header
(document, citation, title) that is searched and embedded with it, because "(b) the direct marketing is in respect
of that person's similar products" means nothing without "PECR reg. 22: Use of electronic mail for direct marketing".
When a chunk starts inside a list, the lead-in it hangs from ("A person may send ... where—") is carried over.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TARGET_WORDS = 220
MAX_WORDS = 320
MIN_TAIL = 60
MAX_BLOCK = 380  # a single paragraph longer than this is split at sentences


@dataclass
class Chunk:
    id: str
    provision: str
    doc: str
    label: str
    title: str
    path: str
    url: str
    kind: str
    refs: list[str]
    blocks: list[dict]  # [{"ref": "(3)(a)", "text": "..."}]: the citable units handed to Claude
    context: str = ""  # carried-over lead-in, shown in grey, not citable
    header: str = ""
    words: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(b["text"] for b in self.blocks)

    @property
    def cite(self) -> str:
        """'PECR reg. 22(3)', or a range across top-level paragraphs: '15 U.S.C. § 7704(a)–(b)'."""
        refs = [fmt_ref(r) for r in self.refs if r]
        if not refs:
            return self.label
        a, b = _top(refs[0]), _top(refs[-1])
        return f"{self.label}{a}" if a == b else f"{self.label}{a}–{b}"


def fmt_ref(ref: str) -> str:
    """GDPR-style '1(a)' becomes '(1)(a)'; '(3)(a)' stays."""
    m = re.match(r"^(\d+[a-z]?)(.*)$", ref)
    return f"({m.group(1)}){m.group(2)}" if m else ref


def _top(ref: str) -> str:
    m = re.match(r"^\([^)]+\)", ref)
    return m.group(0) if m else ref


def _render(b: dict) -> str:
    last = re.findall(r"\(([^)]+)\)$", b["ref"]) if b["ref"] else []
    label = f"({last[0]}) " if last else (f"{b['ref']}. " if b["ref"] and b["ref"].isdigit() else "")
    head = f"{b['heading']}. " if b.get("heading") else ""
    return f"{label}{head}{b['text']}".strip()


def _split_long(block: dict) -> list[dict]:
    words = block["text"].split()
    if len(words) <= MAX_BLOCK:
        return [block]
    sentences = re.split(r"(?<=[.;:])\s+(?=[A-Z(“\"])", block["text"])
    parts, cur = [], []
    for s in sentences:
        if cur and len(" ".join(cur + [s]).split()) > TARGET_WORDS:
            parts.append(" ".join(cur))
            cur = []
        cur.append(s)
    if cur:
        parts.append(" ".join(cur))
    return [{**block, "text": p, "heading": block.get("heading", "") if i == 0 else ""} for i, p in enumerate(parts)]


def _ancestors(blocks: list[dict], i: int) -> list[dict]:
    ref = blocks[i]["ref"]
    out = []
    for b in blocks[:i]:
        r = b["ref"]
        if r and ref.startswith(r) and r != ref and b["text"].rstrip().endswith(("—", "-", ":", "where", "following", "unless", "must", "shall")):
            out.append(b)
    return out[-1:]


def chunk_provision(p: dict, doc_title: str) -> list[Chunk]:
    blocks = [x for b in p["blocks"] for x in _split_long(b)]
    groups: list[list[int]] = []
    cur: list[int] = []
    words = 0
    for i, b in enumerate(blocks):
        n = len(_render(b).split())
        if cur and (words + n > MAX_WORDS or (words >= TARGET_WORDS and n > 25)):
            groups.append(cur)
            cur, words = [], 0
        cur.append(i)
        words += n
    if cur:
        if groups and words < MIN_TAIL and sum(len(_render(blocks[j]).split()) for j in groups[-1]) + words <= MAX_WORDS + MIN_TAIL:
            groups[-1] += cur
        else:
            groups.append(cur)
    out = []
    for n, idx in enumerate(groups):
        sel = [blocks[i] for i in idx]
        lead = _ancestors(blocks, idx[0])
        context = " ".join(_render(b) for b in lead)
        path = f" · {p['path']}" if p.get("path") else ""
        c = Chunk(
            id=f"{p['id']}#{n}", provision=p["id"], doc=p["doc"], label=p["label"], title=p["title"], path=p.get("path", ""),
            url=p["url"], kind=p["kind"], refs=[b["ref"] for b in sel], blocks=[{"ref": b["ref"], "text": _render(b)} for b in sel],
            context=context, header=f"{doc_title}{path} — {p['label']}: {p['title']}",
        )
        c.words = len(c.text.split())
        if c.words >= 3:
            out.append(c)
    return out
