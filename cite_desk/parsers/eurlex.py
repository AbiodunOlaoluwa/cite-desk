"""EU acts from EUR-Lex. Two layouts: the Official Journal text (articles and recitals) and consolidated versions."""
from __future__ import annotations

import re

import warnings

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from .common import Block, Provision, clean

CELEX = re.compile(r"CELEX:(\w+)|celex/(\w+)")


def _celex(doc) -> str:
    m = CELEX.search(doc.fetch_url)
    return m.group(1) or m.group(2)


def _titlecase(s: str) -> str:
    return " ".join(w if re.fullmatch(r"[IVXLC]+", w) else w.capitalize() for w in s.split())


def _url(doc, anchor: str) -> str:
    return f"https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:{_celex(doc)}#{anchor}"


def _cells(table: Tag) -> tuple[str, Tag] | None:
    tds = table.find("tr").find_all("td", recursive=False) if table.find("tr") else []
    if len(tds) != 2:
        return None
    return clean(tds[0].get_text(" ")), tds[1]


def _walk(node: Tag, para: str, blocks: list[Block]):
    """Article bodies are paragraphs ('1.   Text') and two-column tables for points ('(a)' | text), nested."""
    for child in node.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "table":
            cells = _cells(child)
            if not cells:
                continue
            label, body = cells
            ref = f"{para}{label}" if label.startswith("(") else para
            direct = [p for p in body.find_all("p", recursive=False)]
            text = clean(" ".join(p.get_text(" ") for p in direct))
            blocks.append(Block(ref=ref, text=text))
            for inner in body.find_all("table", recursive=False):
                _walk_table(inner, ref, blocks)
        elif child.name == "p" and "oj-normal" in (child.get("class") or []):
            text = clean(child.get_text(" "))
            m = re.match(r"^(\d+)\.\s+(.*)", text)
            if m:
                para = m.group(1)
                blocks.append(Block(ref=para, text=m.group(2)))
            elif text:
                blocks.append(Block(ref=para, text=text))
        elif child.name == "div" and not (child.get("class") or [""])[0].startswith("eli-title"):
            sub_para = para
            m = re.match(r"^\d{3}\.(\d{3})$", child.get("id") or "")
            if m:
                sub_para = str(int(m.group(1)))
            _walk(child, sub_para, blocks)
        para = para


def _walk_table(table: Tag, parent: str, blocks: list[Block]):
    cells = _cells(table)
    if not cells:
        return
    label, body = cells
    ref = f"{parent}{label}"
    blocks.append(Block(ref=ref, text=clean(" ".join(p.get_text(" ") for p in body.find_all("p", recursive=False)))))
    for inner in body.find_all("table", recursive=False):
        _walk_table(inner, ref, blocks)


def parse_oj(raw: str, doc) -> list[Provision]:
    soup = BeautifulSoup(raw, "lxml")
    out: list[Provision] = []
    for div in soup.select('div.eli-subdivision[id^="rct_"]'):
        num = div["id"].split("_")[1]
        cells = _cells(div.find("table"))
        text = clean(cells[1].get_text(" ")) if cells else clean(div.get_text(" "))
        out.append(Provision(id=f"{doc.id}/rct-{num}", doc=doc.id, kind="recital", num=num, label=f"{doc.short} Recital {num}",
                             title=f"Recital {num}", url=_url(doc, f"rct_{num}"), blocks=[Block(ref="", text=text)]))
    for div in soup.select('div.eli-subdivision[id^="art_"]'):
        num = div["id"].split("_")[1]
        sti = div.select_one("p.oj-sti-art")
        title = clean(sti.get_text(" ")) if sti else ""
        chapter = div.find_parent("div", id=re.compile(r"^cpt_"))
        path = ""
        if chapter:
            heads = [clean(p.get_text(" ")) for p in chapter.find_all("p", class_=re.compile("oj-ti-section"), limit=2)]
            path = " · ".join(_titlecase(h) if h.isupper() else h for h in heads)
        prov = Provision(id=f"{doc.id}/art-{num}", doc=doc.id, kind="article", num=num, label=f"{doc.short} Art. {num}",
                         title=title, url=_url(doc, f"art_{num}"), path=path)
        _walk(div, "", prov.blocks)
        out.append(prov)
    return out


def parse_consolidated(raw: str, doc) -> list[Provision]:
    soup = BeautifulSoup(raw, "lxml")
    for junk in soup.select("p.modref, a[href^='#E'], span.superscript, p.arrow"):
        junk.decompose()
    out: list[Provision] = []
    for div in soup.select('div.eli-subdivision[id^="art_"]'):
        num = div["id"].split("_")[1]
        sti = div.select_one("p.stitle-article-norm")
        title = re.sub(r"[►◄]\s*\w*\d*\s*", "", clean(sti.get_text(" "))).strip() if sti else ""
        prov = Provision(id=f"{doc.id}/art-{num}", doc=doc.id, kind="article", num=num, label=f"{doc.short} Art. {num}",
                         title=title, url=_url(doc, f"art_{num}"))
        norms = div.find_all("div", class_="norm", recursive=False)
        if not norms:
            norms = [n for n in div.find_all("div", class_="norm") if "inline-element" not in n.get("class", [])]
        for norm in norms:
            lab = norm.find("span", class_="no-parag")
            ref = clean(lab.get_text(" ")).rstrip(".") if lab else ""
            if lab:
                lab.extract()
            text = clean(norm.get_text(" "))
            if text:
                prov.blocks.append(Block(ref=ref, text=text))
        if not prov.blocks:
            body = [clean(p.get_text(" ")) for p in div.find_all("p", class_="norm")]
            prov.blocks = [Block(ref="", text=t) for t in body if t]
        if prov.blocks:
            out.append(prov)
    return out
