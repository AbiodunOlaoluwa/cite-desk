"""UK legislation (legislation.gov.uk CLML XML) and Canadian federal acts (Justice Laws XML)."""
from __future__ import annotations

import re

from lxml import etree

from .common import Block, Provision, clean

UK = "{http://www.legislation.gov.uk/namespaces/legislation}"
SKIP_UK = {f"{UK}CommentaryRef", f"{UK}FootnoteRef", f"{UK}Pnumber", f"{UK}Title"}


def _uk_text(el) -> str:
    parts = []

    def walk(e):
        if e.tag in SKIP_UK or e.tag.startswith(f"{UK}P") and e.tag[len(UK):][1:2].isdigit() and e is not el:
            return  # nested P2/P3 are their own blocks
        if e.text:
            parts.append(e.text)
        for c in e:
            walk(c)
            if c.tail:
                parts.append(c.tail)

    walk(el)
    return clean(" ".join(parts))


def _uk_level(el, prefix: str, blocks: list[Block]):
    """P1/P2/P3/P4: each has a Pnumber and a PNpara holding Text and deeper levels."""
    for child in el:
        tag = child.tag[len(UK):] if child.tag.startswith(UK) else ""
        if not re.fullmatch(r"P[2-6]", tag):
            continue
        pn = child.find(f"{UK}Pnumber")
        ref = f"{prefix}({clean(''.join(pn.itertext()))})" if pn is not None else prefix
        para = child.find(f"{UK}{tag}para")
        if para is None:
            continue
        lead = " ".join(_uk_text(t) for t in para.findall(f"{UK}Text"))
        blocks.append(Block(ref=ref, text=clean(lead)))
        _uk_lists(para, ref, blocks)
        _uk_level(para, ref, blocks)


def _uk_lists(para, ref: str, blocks: list[Block]):
    """Definitions and lists sit in UnorderedList/OrderedList items: one block each, the defined term as heading."""
    for lst in para:
        if lst.tag not in (f"{UK}UnorderedList", f"{UK}OrderedList"):
            continue
        for item in lst.findall(f"{UK}ListItem"):
            text = " ".join(_uk_text(t) for t in item.iter(f"{UK}Text"))
            term = item.find(f".//{UK}Term")
            if text:
                blocks.append(Block(ref=ref, text=text, heading=clean("".join(term.itertext())) if term is not None else ""))


def parse_uk(raw: bytes | str, doc) -> list[Provision]:
    root = etree.fromstring(raw.encode() if isinstance(raw, str) else raw)
    out = []
    for group in root.iter(f"{UK}P1group"):
        title_el = group.find(f"{UK}Title")
        title = clean("".join(title_el.itertext())) if title_el is not None else ""
        for p1 in group.findall(f"{UK}P1"):
            pid = p1.get("id") or ""
            m = re.match(r"regulation-(\d+[A-Z]*)$", pid)
            if not m:
                continue
            num = m.group(1)
            prov = Provision(id=f"{doc.id}/reg-{num}", doc=doc.id, kind="regulation", num=num, label=f"{doc.short} reg. {num}",
                             title=title, url=f"https://www.legislation.gov.uk/uksi/2003/2426/regulation/{num}")
            para = p1.find(f"{UK}P1para")
            if para is not None:
                lead = " ".join(_uk_text(t) for t in para.findall(f"{UK}Text"))
                if lead:
                    prov.blocks.append(Block(ref="", text=lead))
                _uk_lists(para, "", prov.blocks)
                _uk_level(para, "", prov.blocks)
            if any(b.text.strip(" .…") for b in prov.blocks):
                out.append(prov)
    return out


CA_SKIP = {"HistoricalNote", "Footnote", "MarginalNote", "Label", "FootnoteRef"}


def _ca_text(el) -> str:
    parts = []

    def walk(e):
        if e.tag in CA_SKIP:
            return
        if e.text:
            parts.append(e.text)
        for c in e:
            walk(c)
            if c.tail:
                parts.append(c.tail)

    walk(el)
    return clean(" ".join(parts))


CA_LEVELS = ("Subsection", "Paragraph", "Subparagraph", "Clause", "Subclause")


def _ca_walk(el, prefix: str, blocks: list[Block]):
    for child in el:
        if child.tag in CA_LEVELS:
            lab = child.find("Label")
            ref = prefix + (clean(lab.text or "") if lab is not None else "")
            note = child.find("MarginalNote")
            text = " ".join(_ca_text(t) for t in child.findall("Text"))
            blocks.append(Block(ref=ref, text=text, heading=clean(note.text or "") if note is not None else ""))
            for d in child.findall("Definition"):
                blocks.append(Block(ref=ref, text=_ca_text(d)))
            _ca_walk(child, ref, blocks)
        elif child.tag == "Definition":
            blocks.append(Block(ref=prefix, text=_ca_text(child)))


def parse_ca(raw: bytes | str, doc) -> list[Provision]:
    root = etree.fromstring(raw.encode() if isinstance(raw, str) else raw)
    body = root.find("Body")
    out, path = [], {}
    stop = False
    for el in body:
        if el.tag == "Heading":
            level = int(el.get("level") or 1)
            title = clean(" ".join(el.find("TitleText").itertext())) if el.find("TitleText") is not None else ""
            path = {k: v for k, v in path.items() if k < level}
            path[level] = title
            if level == 1 and re.search(r"amendments|coming into force|transitional", title, re.I):
                stop = True  # the rest of the Act amends other Acts
            elif level == 1:
                stop = False
            continue
        if el.tag != "Section" or stop:
            continue
        lab = el.find("Label")
        num = clean(lab.text or "") if lab is not None else ""
        note = el.find("MarginalNote")
        prov = Provision(id=f"{doc.id}/s-{num}", doc=doc.id, kind="section", num=num, label=f"{doc.short} s. {num}",
                         title=clean(note.text or "") if note is not None else "",
                         url=f"https://laws-lois.justice.gc.ca/eng/acts/E-1.6/section-{num}.html",
                         path=" · ".join(path[k] for k in sorted(path)))
        lead = " ".join(_ca_text(t) for t in el.findall("Text"))
        if lead:
            prov.blocks.append(Block(ref="", text=lead))
        for d in el.findall("Definition"):
            prov.blocks.append(Block(ref="", text=_ca_text(d)))
        _ca_walk(el, "", prov.blocks)
        if prov.blocks and not re.match(r"^\[?(repealed|spent)", prov.blocks[0].text, re.I):
            out.append(prov)
    return out
