from cite_desk.chunk import MAX_BLOCK, MAX_WORDS, MIN_TAIL, chunk_provision, fmt_ref
from cite_desk.corpus import provisions
from cite_desk.sources import DOCS

ALL = [c for col in ("outreach", "lagos-tenancy") for p in provisions(col) for c in chunk_provision(p, DOCS[p["doc"]].title)]


def test_chunks_stay_inside_one_provision_and_are_bounded():
    for c in ALL:
        assert c.id.startswith(c.provision + "#")
        assert c.words <= MAX_WORDS + MIN_TAIL + MAX_BLOCK, c.id


def test_lead_in_is_carried_into_a_mid_list_chunk():
    reg22 = [c for c in ALL if c.provision == "uk-pecr/reg-22"]
    later = [c for c in reg22 if c.context]
    assert later and "charity may send" in later[0].context


def test_header_names_document_and_provision():
    c = next(c for c in ALL if c.provision == "lagos-tenancy-law/s-4")
    assert "Tenancy Law" in c.header and "Advance Rent" in c.header


def test_cite_formats():
    assert fmt_ref("1(a)") == "(1)(a)" and fmt_ref("(3)(a)") == "(3)(a)"
    c = next(c for c in ALL if c.provision == "lagos-tenancy-law/s-4")
    assert c.cite == "Tenancy Law 2011, s. 4(1)–(5)"
