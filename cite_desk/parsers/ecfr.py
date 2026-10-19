"""CFR sections from the eCFR API's XML. Paragraphs follow the CFR scheme: (a), (1), (i), (A)."""
from __future__ import annotations

import re

from lxml import etree

from .common import Block, Outline, Provision, clean, leading_labels

SCHEME = ("lower", "digit", "roman", "upper")


def _text(el) -> str:
    return clean(" ".join(el.itertext()))


def parse(raw: str, doc) -> list[Provision]:
    root = etree.fromstring(raw.encode("utf-8") if isinstance(raw, str) else raw)
    title_no = re.search(r"title-(\d+)", doc.fetch_url).group(1)
    out = []
    for sec in root.iter("DIV8"):
        if sec.get("TYPE") != "SECTION":
            continue
        num = sec.get("N")
        head = _text(sec.find("HEAD"))
        title = re.sub(r"^§\s*[\d.]+\s*", "", head).rstrip(".")
        part = num.split(".")[0]
        prov = Provision(
            id=f"{doc.id}/{num}", doc=doc.id, kind="section", num=num, label=f"{title_no} CFR § {num}", title=title,
            url=f"https://www.ecfr.gov/current/title-{title_no}/part-{part}/section-{num}",
        )
        outline = Outline(SCHEME)
        for el in sec:
            if el.tag not in ("P", "FP"):
                continue
            text = _text(el)
            if not text:
                continue
            labels, rest = leading_labels(text)
            ref = outline.push(labels)
            # An italic run right after the label is the paragraph's heading ("(c) Telephone solicitation. ...").
            heading = ""
            first_i = el.find("I")
            if labels and first_i is not None and rest.startswith(_text(first_i)):
                heading = _text(first_i).rstrip(".")
                rest = rest[len(_text(first_i)):].strip()
            prov.blocks.append(Block(ref=ref, text=rest, heading=heading))
        out.append(prov)
    return out
