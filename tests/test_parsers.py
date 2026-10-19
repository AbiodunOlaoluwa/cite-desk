import json
from pathlib import Path

from cite_desk.corpus import CORPUS, provisions
from cite_desk.parsers.common import Outline, leading_labels
from cite_desk.parsers.pdflaw import _blocks

P = {p["id"]: p for c in ("outreach", "lagos-tenancy") for p in provisions(c)}


def text(pid):
    return " ".join(b["text"] for b in P[pid]["blocks"])


def test_outline_nests_us_code_labels():
    o = Outline(("lower", "digit", "upper", "roman", "ROMAN"))
    assert [o.push([x]) for x in ["a", "1", "A", "i", "I", "II", "ii", "B", "2"]][-3:] == ["(a)(1)(A)(ii)", "(a)(1)(B)", "(a)(2)"]


def test_roman_i_after_h_is_a_letter_otherwise_a_numeral():
    o = Outline(("lower", "digit", "roman", "upper"))
    assert [o.push([x]) for x in ["h", "i"]] == ["(h)", "(i)"]
    o = Outline(("digit", "lower", "roman"))
    assert [o.push([x]) for x in ["1", "a", "i", "ii"]][-1] == "(1)(a)(ii)"


def test_leading_labels():
    assert leading_labels("(a)(1) It is unlawful") == (["a", "1"], "It is unlawful")
    assert leading_labels("No label here") == ([], "No label here")


def test_pdf_numbers_in_brackets_are_not_labels():
    # "six (6)\nmonths" puts "(6)" at the start of a line in the gazette PDF
    blocks = _blocks(["(1) rent in excess of six", "(6) months from a monthly tenant", "(2) It shall be unlawful"])
    assert [b.ref for b in blocks] == ["(1)", "(2)"]
    assert "six (6) months" in blocks[0].text


def test_every_provision_is_citable():
    for p in P.values():
        assert p["label"] and p["url"].startswith("http")
        assert any(b["text"].strip() for b in p["blocks"]), p["id"]


def test_gdpr_has_every_article_and_recital():
    kinds = [p["kind"] for p in P.values() if p["doc"] == "eu-gdpr"]
    assert kinds.count("article") == 99 and kinds.count("recital") == 173


def test_lagos_exemptions_and_all_sections():
    assert len([p for p in P.values() if p["doc"] == "lagos-tenancy-law"]) == 49
    s1 = text("lagos-tenancy-law/s-1")
    for place in ("Apapa", "Ikeja GRA", "Ikoyi", "Victoria Island"):
        assert place in s1
    assert P["lagos-tenancy-law/s-13"]["title"] == "Length of Notice"


def test_uk_definitions_are_parsed():
    defs = {b.get("heading"): b["text"] for b in P["uk-pecr/reg-2"]["blocks"] if b.get("heading")}
    assert "unincorporated body" in defs["individual"]


def test_us_paragraph_refs():
    refs = [b["ref"] for b in P["us-canspam-act/7704"]["blocks"]]
    assert "(a)(5)(A)(iii)" in refs
    assert "valid physical postal address" in next(b["text"] for b in P["us-canspam-act/7704"]["blocks"] if b["ref"] == "(a)(5)(A)(iii)")


def test_manifest_records_sources_and_terms():
    m = json.loads((CORPUS / "manifest.json").read_text())
    for c in m["collections"].values():
        for d in c["docs"]:
            assert d["licence"] and d["source"].startswith("http") and d["provisions"] > 0
