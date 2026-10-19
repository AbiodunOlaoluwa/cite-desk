"""Shared pieces for the parsers: one provision shape, text cleaning, and paragraph-label resolution."""
from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field

ROMAN = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii",
         "xviii", "xix", "xx", "xxi", "xxii", "xxiii", "xxiv", "xxv"}


@dataclass
class Block:
    ref: str  # "(a)(3)(A)", "1(b)", "" for an unnumbered lead-in
    text: str
    heading: str = ""


@dataclass
class Provision:
    """The unit a citation points at: a section, article, regulation, recital or guidance heading."""

    id: str  # "eu-gdpr/art-6"
    doc: str
    kind: str  # section | article | recital | regulation | schedule | guidance
    num: str
    label: str  # "GDPR Art. 6"
    title: str
    url: str
    path: str = ""
    blocks: list[Block] = field(default_factory=list)

    def to_json(self) -> dict:
        d = asdict(self)
        d["blocks"] = [b for b in d["blocks"] if b["text"] or b["heading"]]
        return d


def clean(text: str) -> str:
    text = html.unescape(text).replace("\u00ad", "").replace("\u00a0", " ").replace("\u2009", " ").replace("\u200b", "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return text.strip()


LEAD = re.compile(r"^\s*((?:\((?:[a-z]{1,4}|[A-Z]{1,4}|[0-9]{1,3}[A-Z]?)\)\s*)+)")


def leading_labels(text: str) -> tuple[list[str], str]:
    """'(a)(1) It is unlawful' -> (['a', '1'], 'It is unlawful')."""
    m = LEAD.match(text)
    if not m:
        return [], text
    labels = re.findall(r"\(([^)]+)\)", m.group(1))
    return labels, text[m.end():].strip()


class Outline:
    """Tracks where we are in a nested paragraph scheme, so '(A)' after '(a)(3)' becomes '(a)(3)(A)'.

    `scheme` lists the label kinds by depth, e.g. US Code: lower, digit, upper, roman, ROMAN. Levels can be skipped
    (a section that starts at '(a)' with no '(1)'), so the stack is sparse: (depth, label) pairs.
    The ambiguous case is '(i)', '(v)', '(x)': it is the next letter only when the letter before it ('h', 'u', 'w')
    is open at that depth; otherwise it is a roman numeral.
    """

    def __init__(self, scheme: tuple[str, ...]):
        self.scheme = scheme
        self.stack: list[tuple[int, str]] = []

    @staticmethod
    def kinds(label: str) -> list[str]:
        out = []
        if label.isdigit() or re.fullmatch(r"\d+[A-Z]", label):
            out.append("digit")
        if label.islower() and label in ROMAN:
            out.append("roman")
        if re.fullmatch(r"[a-z]", label) or re.fullmatch(r"([a-z])\1", label):
            out.append("lower")
        if label.isupper() and label.lower() in ROMAN:
            out.append("ROMAN")
        if re.fullmatch(r"[A-Z]", label) or re.fullmatch(r"([A-Z])\1", label):
            out.append("upper")
        return out

    def _open(self, depth: int) -> str | None:
        return next((lab for d, lab in self.stack if d == depth), None)

    def _depth_for(self, label: str) -> int | None:
        kinds = self.kinds(label)
        candidates = [i for i, k in enumerate(self.scheme) if k in kinds]
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        by_kind = {self.scheme[i]: i for i in candidates}
        if "lower" in by_kind and ("roman" in by_kind):
            prev = self._open(by_kind["lower"])
            if prev and len(prev) == 1 and len(label) == 1 and ord(label) == ord(prev) + 1:
                return by_kind["lower"]
            return by_kind["roman"]
        if "upper" in by_kind and "ROMAN" in by_kind:
            prev = self._open(by_kind["upper"])
            if prev and len(prev) == 1 and len(label) == 1 and ord(label) == ord(prev) + 1:
                return by_kind["upper"]
            return by_kind["ROMAN"]
        return min(candidates)

    @staticmethod
    def _is_first(label: str, kind: str) -> bool:
        return label == {"digit": "1", "roman": "i", "ROMAN": "I", "lower": "a", "upper": "A"}[kind]

    @staticmethod
    def _is_next(prev: str, label: str, kind: str) -> bool:
        if kind in ("roman", "ROMAN"):
            p, l = prev.lower(), label.lower()
            return p in ROMAN and l in ROMAN and _roman_value(l) == _roman_value(p) + 1
        if kind == "digit":
            a, b = re.match(r"\d+", prev), re.match(r"\d+", label)
            return bool(a and b) and (int(b.group()) == int(a.group()) + 1 or (b.group() == a.group() and label != prev))
        return len(prev) == 1 and len(label) == 1 and ord(label.lower()) == ord(prev.lower()) + 1

    def push(self, labels: list[str]) -> str:
        for label in labels:
            depth = self._depth_for(label)
            if depth is None:
                continue
            self.stack = [(d, lab) for d, lab in self.stack if d < depth] + [(depth, label)]
        return self.ref()

    def ref(self) -> str:
        return "".join(f"({lab})" for _, lab in self.stack)


def _roman_value(s: str) -> int:
    vals = {"i": 1, "v": 5, "x": 10}
    total, prev = 0, 0
    for ch in reversed(s.lower()):
        v = vals.get(ch, 0)
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]
