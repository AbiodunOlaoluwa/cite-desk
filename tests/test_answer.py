from types import SimpleNamespace as NS

from cite_desk import answer as A


def usage():
    return NS(input_tokens=900, output_tokens=120, cache_read_input_tokens=0, cache_creation_input_tokens=0)


class FakeClient:
    def __init__(self, content, stop="end_turn"):
        self.beta = NS(messages=NS(create=self.create))
        self.content, self.stop, self.sent = content, stop, None

    def create(self, **kw):
        self.sent = kw
        return NS(content=self.content, stop_reason=self.stop, usage=usage())


def hits(engine):
    return engine.search("lagos-tenancy", "How much rent in advance from a new yearly tenant?")["hits"]


def test_claude_citations_map_to_exact_paragraphs(engine):
    hs = hits(engine)
    top = hs[0]
    para = next(i for i, b in enumerate(top["blocks"]) if b["ref"] == "(3)")
    offset = 1 if top.get("context") else 0
    cite = NS(type="search_result_location", search_result_index=0, start_block_index=para + offset,
              end_block_index=para + offset + 1, cited_text=top["blocks"][para]["text"])
    client = FakeClient([NS(type="text", text="A landlord may not take more than a year's rent from a new tenant.", citations=[cite])])
    a = A.answer_claude("How much rent in advance?", hs, "lagos-tenancy", client=client)
    assert a.status == "answered" and a.citations[0].cite == "Tenancy Law 2011, s. 4(3)"
    assert a.usage["cost_micros"] == 900 * 5 + 120 * 25
    results = [c for c in client.sent["messages"][0]["content"] if c["type"] == "search_result"]
    assert results[0]["citations"] == {"enabled": True} and len(results) == len(hs)


def test_an_answer_that_cites_nothing_is_not_shown_as_an_answer(engine):
    client = FakeClient([NS(type="text", text="Usually one year, in my experience.", citations=None)])
    a = A.answer_claude("How much rent in advance?", hits(engine), "lagos-tenancy", client=client)
    assert a.status == "refused" and "cited none" in a.reason


def test_model_refusal_is_a_refusal(engine):
    a = A.answer_claude("x?", hits(engine), "lagos-tenancy", client=FakeClient([], stop="refusal"))
    assert a.status == "refused"


def test_extractive_quotes_exact_text(engine):
    hs = hits(engine)
    a = A.answer_extractive("rent in excess of one year from a new tenant", hs, engine.reranker)
    texts = {b["text"] for h in hs for b in h["blocks"]}
    assert a.citations and all(c.quote in texts for c in a.citations)
    assert A.answer_extractive("x", hs, engine.reranker, weak=True).status == "weak"


def test_refusal_says_what_the_collection_covers():
    a = A.refusal("outreach", {"query": {"filtered_to": ["UK"]}}, "below the floor")
    assert "UK documents" in a.segments[0]["text"] and "Canada: CASL" in a.segments[0]["text"]
