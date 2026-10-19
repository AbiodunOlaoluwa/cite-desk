"""Laws that only exist as PDFs: the Lagos Tenancy Law 2011 (gazette) and the NDPC's GAID 2025.

Text comes from poppler's `pdftotext -layout`, which keeps words whole and lines in reading order. It is only
needed to build the corpus; the app reads the committed JSONL.
"""
from __future__ import annotations

import re
import subprocess
import tempfile

from .common import Block, Outline, Provision, clean

LABEL = re.compile(r"^\s*(?:\d{1,2}\.\s*-\s*)?(?:\((\w{1,4})\)|(\b[a-z]{1,2}|\b[ivx]{1,5})\))\s*")
SCHEME = ("digit", "lower", "roman")


def pdftotext(raw: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(raw)
        f.flush()
        return subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, text=True, check=True).stdout


def _plausible(outline: Outline, label: str) -> bool:
    """'six (6)\nmonths' puts '(6)' at the start of a line; a real label continues its level's sequence."""
    depth = outline._depth_for(label)
    if depth is None:
        return False
    kind = outline.scheme[depth]
    prev = outline._open(depth)
    if prev is None:
        return Outline._is_first(label, kind) if kind in ("digit", "lower", "roman", "upper", "ROMAN") else True
    return Outline._is_next(prev, label, kind)


def _blocks(lines: list[str], scheme=SCHEME) -> list[Block]:
    """Split a provision's lines into blocks at each line-leading label: '(1)', '(a)', 'a)', '(iii)'."""
    outline, blocks, buf, ref = Outline(scheme), [], [], ""
    for line in lines:
        m = LABEL.match(line)
        label = (m.group(1) or m.group(2)) if m else None
        if m and _plausible(outline, label):
            if buf and clean(" ".join(buf)):
                blocks.append(Block(ref=ref, text=clean(" ".join(buf))))
            ref = outline.push([label])
            buf = [line[m.end():]]
        else:
            buf.append(line)
    if buf and clean(" ".join(buf)):
        blocks.append(Block(ref=ref, text=clean(" ".join(buf))))
    return blocks


# ---- Lagos Tenancy Law 2011 ---------------------------------------------------------------------------------

FURNITURE = re.compile(r"^\s*(Supplement to Lagos State|A\d{3}\s*$|\d{1,3}\s*$)")


def _arrangement(text: str) -> dict[str, str]:
    """The 'Arrangement of Sections' is three columns of 'N. Title', titles wrapping onto the next line."""
    start = text.index("Arrangement of Sections")
    end = text.index("Supplement to Lagos State", start)
    titles: dict[str, list[str]] = {}
    columns: list[tuple[int, str]] = []  # (start offset, section number) for the current row
    for line in text[start:end].splitlines()[1:]:
        if not line.strip():
            continue
        hits = list(re.finditer(r"(?<!\S)(\d{1,2})\.\s+", line))
        if hits:
            columns = []
            for i, m in enumerate(hits):
                stop = hits[i + 1].start() if i + 1 < len(hits) else len(line)
                titles[m.group(1)] = [line[m.end():stop].strip()]
                columns.append((m.start(), m.group(1)))
        else:
            for m in re.finditer(r"\S(?:.*?\S)?(?=\s{3,}|$)", line):
                owner = max((c for c in columns if c[0] <= m.start() + 2), default=None, key=lambda c: c[0])
                if owner:
                    titles[owner[1]].append(m.group(0).strip())
    return {k: clean(" ".join(v)).rstrip(".").replace(" in ", " in ") for k, v in titles.items()}


def parse_lagos(raw: bytes, doc) -> list[Provision]:
    text = pdftotext(raw)
    titles = _arrangement(text)
    body = text[text.index("enacts as follows"):].splitlines()[1:]
    body = [ln for ln in body if not FURNITURE.match(ln)]
    out: list[Provision] = []
    expected, cur, lines = 1, None, []

    def flush():
        if cur is None:
            return
        cur.blocks = _blocks(lines)
        if cur.num == "47":  # Interpretation: one block per defined term
            cur.blocks = _definitions(lines)
        out.append(cur)

    for line in body:
        m = re.match(r"^\s*(\d{1,2})(?:\.(?=\s*-|\s|\()|(?=-\())", line)
        if m and int(m.group(1)) == expected:
            flush()
            num = m.group(1)
            cur = Provision(id=f"{doc.id}/s-{num}", doc=doc.id, kind="section", num=num, label=f"{doc.short}, s. {num}",
                            title=titles.get(num, ""), url=doc.fetch_url)
            rest = line[m.end():]
            lines = [re.sub(r"^\s*-\s*", "", rest)]
            expected += 1
            continue
        if cur is not None:
            lines.append(line)
    flush()
    return out


def _definitions(lines: list[str]) -> list[Block]:
    blocks, buf, term = [], [], ""
    for line in lines:
        m = re.match(r'^\s*["“]([^"”]+)["”]', line)
        if m:
            if buf:
                blocks.append(Block(ref="", heading=term, text=clean(" ".join(buf))))
            term, buf = m.group(1), [line]
        else:
            buf.append(line)
    if buf:
        blocks.append(Block(ref="", heading=term, text=clean(" ".join(buf))))
    return blocks


# ---- NDPC GAID 2025 -----------------------------------------------------------------------------------------

def parse_gaid(raw: bytes, doc) -> list[Provision]:
    text = pdftotext(raw)
    lines = [ln for ln in text.splitlines() if not re.fullmatch(r"\s*\d{1,3}\s*", ln)]
    # The arrangement lists every article first; the body starts at the second "Article 1:".
    starts = [i for i, ln in enumerate(lines) if re.match(r"^\s*Article 1:", ln)]
    schedule_titles = {}
    for ln in lines[:starts[1]]:
        m = re.match(r"^\s*Schedule (\d+)\s*[-–]\s*(.+)$", ln)
        if m:
            schedule_titles[m.group(1)] = clean(m.group(2))
    body = lines[starts[1]:]
    out: list[Provision] = []
    cur, buf, title_open = None, [], False

    def flush():
        if cur is not None:
            cur.blocks = _blocks(buf)
            out.append(cur)

    for ln in body:
        a = re.match(r"^\s*Article (\d+):\s*(.+)$", ln)
        s = re.match(r"^\s*SCHEDULE\s+(\d+)\b\s*[-–:]?\s*(.*)$", ln, re.I)
        if a and (cur is None or int(a.group(1)) == int(cur.num) + 1):
            flush()
            num = a.group(1)
            cur = Provision(id=f"{doc.id}/art-{num}", doc=doc.id, kind="article", num=num, label=f"{doc.short}, Art. {num}",
                            title=clean(a.group(2)), url=doc.fetch_url)
            buf, title_open = [], True
            continue
        if s and cur is not None and (cur.kind == "article" and int(cur.num) >= 52 or cur.kind == "schedule"):
            flush()
            num = s.group(1)
            cur = Provision(id=f"{doc.id}/sch-{num}", doc=doc.id, kind="schedule", num=num, label=f"{doc.short}, Schedule {num}",
                            title=clean(s.group(2)) or schedule_titles.get(num, ""), url=doc.fetch_url)
            buf, title_open = [], True
            continue
        if cur is None:
            continue
        # A wrapped title continues on the next line until the first numbered paragraph.
        if title_open and ln.strip() and not LABEL.match(ln) and len(cur.title) > 40 and not buf:
            cur.title = clean(cur.title + " " + ln)
            title_open = False
            continue
        title_open = False
        buf.append(ln)
    flush()
    return out
