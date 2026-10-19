"""US Code sections from govinfo.gov's HTML. Only the statute text: editorial and statutory notes are dropped."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .common import Block, Outline, Provision, clean, leading_labels

HEAD_LEVEL = {"subsection-head": 0, "paragraph-head": 1, "subparagraph-head": 2, "clause-head": 3, "subclause-head": 4}
SCHEME = ("lower", "digit", "upper", "roman", "ROMAN")


def parse(raw: str, doc) -> list[Provision]:
    out = []
    for seg in re.split(r"<!-- documentid:", raw)[1:]:
        head = re.search(r'<h3 class="section-head">(.*?)</h3>', seg, re.S)
        body = re.search(r"<!-- field-start:statute -->(.*?)<!-- field-end:statute -->", seg, re.S)
        if not head or not body:
            continue
        m = re.match(r"§\s*(\d+[a-z]?)\.\s*(.*)", clean(re.sub("<[^>]+>", "", head.group(1))))
        if not m:
            continue
        num, title = m.group(1), m.group(2).rstrip(".")
        if title.lower().startswith(("repealed", "omitted", "transferred")):
            continue
        title_no = re.search(r"title(\d+)", doc.fetch_url).group(1)
        prov = Provision(
            id=f"{doc.id}/{num}", doc=doc.id, kind="section", num=num, label=f"{title_no} U.S.C. § {num}", title=title,
            url=f"https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title{title_no}-section{num}&num=0&edition=prelim",
        )
        outline, pending = Outline(SCHEME), []
        # govinfo marks PDF page breaks with comments, sometimes mid-word ("busi<!-- PDFPage:2384 -->ness")
        soup = BeautifulSoup(re.sub(r"<!--.*?-->", "", body.group(1), flags=re.S), "lxml")
        for el in soup.find_all(["h4", "p"]):
            cls = (el.get("class") or [""])[0]
            text = clean(el.get_text(" "))
            if not text:
                continue
            labels, rest = leading_labels(text)
            ref = outline.push(labels)
            if el.name == "h4" and cls in HEAD_LEVEL:
                pending.append(rest)
                continue
            if cls.startswith("statutory-body") or cls == "statutory-body":
                prov.blocks.append(Block(ref=ref, text=rest, heading="; ".join(pending)))
                pending = []
        if pending:
            prov.blocks.append(Block(ref=outline.ref(), text="", heading="; ".join(pending)))
        out.append(prov)
    return out
