"""Plain-English guidance pages, split at their headings: the FTC's CAN-SPAM compliance guide."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .common import Block, Provision, clean, slug


def parse_ftc(raw: str, doc) -> list[Provision]:
    soup = BeautifulSoup(raw, "lxml")
    first_q = next(h for h in soup.find_all("h3") if h.get_text(strip=True).startswith("Q."))
    body = first_q.find_parent("div", class_="field__item")
    out: list[Provision] = []
    current = Provision(id=f"{doc.id}/overview", doc=doc.id, kind="guidance", num="1", label=doc.short,
                        title="Overview", url=doc.home_url)

    def flush():
        if current.blocks:
            out.append(current)

    for el in body.find_all(["h2", "h3", "h4", "p", "li"]):
        if el.find_parent(["li"]) is not None and el.name == "p":
            continue  # its text is already in the list item
        text = clean(el.get_text(" "))
        if not text:
            continue
        strong = el.find(["strong", "b"]) if el.name == "p" else None
        numbered = strong is not None and re.match(r"^\d+\.\s", clean(strong.get_text(" "))) and text.startswith(clean(strong.get_text(" "))[:6])
        if el.name in ("h2", "h3", "h4") or numbered:
            heading = clean(strong.get_text(" ")) if numbered else re.sub(r"^Q\.\s*", "", text)
            if heading.upper() in ("MESSAGE A", "MESSAGE B"):
                current.blocks.append(Block(ref="", text=heading + ":"))
                continue
            flush()
            n = len(out) + 1
            current = Provision(id=f"{doc.id}/{slug(heading)}", doc=doc.id, kind="guidance", num=str(n),
                                label=doc.short, title=heading.rstrip("."), url=doc.home_url)
            if numbered:
                rest = text[len(clean(strong.get_text(" "))):].strip()
                if rest:
                    current.blocks.append(Block(ref="", text=rest))
            continue
        lead = el.find(["strong", "b"]) if el.name == "li" else None
        if lead is not None and current.title == "Overview" and text.startswith(clean(lead.get_text(" "))[:12]):
            # Each of the guide's main requirements is a bold-led bullet: give it its own citation.
            heading = clean(lead.get_text(" ")).rstrip(".")
            flush()
            current = Provision(id=f"{doc.id}/{slug(heading)}", doc=doc.id, kind="guidance", num=str(len(out) + 1),
                                label=doc.short, title=heading, url=doc.home_url)
            rest = text[len(clean(lead.get_text(" "))):].strip(" .")
            current.blocks.append(Block(ref="", text=rest))
            flush()
            current = Provision(id=f"{doc.id}/overview-more-{len(out)}", doc=doc.id, kind="guidance", num=str(len(out) + 1),
                                label=doc.short, title="Overview", url=doc.home_url)
            continue
        current.blocks.append(Block(ref="", text=("• " if el.name == "li" else "") + text))
    flush()
    return out
