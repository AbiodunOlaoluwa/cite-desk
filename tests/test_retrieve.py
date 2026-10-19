from cite_desk.retrieve import RRF_K, Settings, _fused_scores, detect_jurisdictions, fts_query


def test_jurisdiction_detection():
    assert detect_jurisdictions("Can I cold email a sole trader in the UK?") == ["UK"]
    assert detect_jurisdictions("GDPR and CASL") == ["EU", "CA"]
    assert detect_jurisdictions("How much notice for a monthly tenant?") == []


def test_fts_query_drops_stopwords_and_quotes_terms():
    q, terms = fts_query("What is the notice for a yearly tenant?")
    assert terms == ["notice", "yearly", "tenant"]
    assert q == '"notice" OR "yearly" OR "tenant"'


def test_rrf_rewards_agreement():
    fused = _fused_scores([(1, 9.0), (2, 8.0)], [(2, 0.9), (3, 0.8)])
    assert fused[2] == 1 / (RRF_K + 2) + 1 / (RRF_K + 1)
    assert max(fused, key=fused.get) == 2


def test_keyword_search_finds_the_section(engine):
    r = engine.retriever("lagos-tenancy").search("Ikoyi Apapa Victoria Island exempted", Settings(mode="bm25"))
    assert r.hits[0]["provision"] == "lagos-tenancy-law/s-1"


def test_filter_keeps_to_the_named_place(engine):
    r = engine.retriever("outreach").search("Can I email a sole trader in the UK without consent?", Settings())
    assert r.trace["query"]["filtered_to"] == ["UK"]
    assert all(h["jurisdiction"] == "UK" for h in r.hits)
    off = engine.retriever("outreach").search("Can I email a sole trader in the UK without consent?", Settings(jurisdiction_filter=False))
    assert off.trace["query"]["filtered_to"] == []


def test_every_stage_is_traced_and_timed(engine):
    r = engine.retriever("outreach").search("unsubscribe within ten business days", Settings())
    assert set(r.trace["ms"]) == {"bm25", "dense", "fuse", "rerank"}
    assert r.trace["reranked"] and len(r.hits) <= 6
    assert all(h["ranks"].keys() == {"bm25", "dense"} for h in r.hits)


def test_at_most_two_chunks_per_provision(engine):
    r = engine.retriever("outreach").search("telephone solicitation calls robocall do-not-call", Settings(k=10))
    counts = {}
    for h in r.hits:
        counts[h["provision"]] = counts.get(h["provision"], 0) + 1
    assert max(counts.values()) <= 2


def test_gate_refuses_below_the_floor_and_flags_weak_matches(engine):
    ret = engine.retriever("lagos-tenancy")
    r = ret.search("Ikoyi exempted areas", Settings(refuse_below=100.0, weak_below=200.0))
    assert r.abstain and not r.weak
    r = ret.search("Ikoyi exempted areas", Settings(refuse_below=-100.0, weak_below=100.0))
    assert r.weak and not r.abstain
    r = ret.search("Ikoyi exempted areas", Settings(refuse_below=-100.0, weak_below=-50.0))
    assert not r.weak and not r.abstain
