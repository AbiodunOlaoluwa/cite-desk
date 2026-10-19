import pytest
from fastapi.testclient import TestClient

from cite_desk import evals, web
from cite_desk.service import Limits


@pytest.fixture()
def client(engine, monkeypatch):
    monkeypatch.setattr(web, "ENGINE", engine)
    return TestClient(web.app)


def test_meta_and_search_and_answer(client):
    m = client.get("/api/meta").json()
    assert m["mode"] == "extractive" and set(m["collections"]) == {"outreach", "lagos-tenancy"}
    s = client.post("/api/search", json={"collection": "lagos-tenancy", "q": "Does the Lagos Tenancy Law apply to a flat in Ikoyi?"}).json()
    assert s["eval"]["id"] == "l02" and "trace" in s
    assert any(h.get("gold") for h in s["hits"])
    a = client.post("/api/answer", json={"collection": "lagos-tenancy", "q": "Does the Lagos Tenancy Law apply to a flat in Ikoyi?"}).json()
    assert a["mode"] == "extractive" and a["status"] in ("answered", "weak", "refused")


def test_bad_input_is_rejected(client):
    assert client.post("/api/search", json={"collection": "nope", "q": "hello there"}).status_code == 404
    assert client.post("/api/search", json={"collection": "outreach", "q": "x"}).status_code == 422


def test_corpus_endpoints(client):
    rows = client.get("/api/collections/lagos-tenancy/docs/lagos-tenancy-law").json()
    assert len(rows) == 49 and rows[0]["chunks"] >= 1
    d = client.get("/api/collections/lagos-tenancy/provision", params={"id": "lagos-tenancy-law/s-13"}).json()
    assert d["chunks"] and d["chunks"][0]["blocks"]
    assert client.get("/api/collections/lagos-tenancy/provision", params={"id": "nope"}).status_code == 404


def test_spending_limits():
    lim = Limits()
    import cite_desk.service as svc
    old = svc.PER_IP_HOURLY
    svc.PER_IP_HOURLY = 2
    try:
        assert lim.take("1.2.3.4") is None and lim.take("1.2.3.4") is None
        assert "hourly limit" in lim.take("1.2.3.4")
        assert lim.take("5.6.7.8") is None
    finally:
        svc.PER_IP_HOURLY = old


def test_gold_matching_respects_paragraphs():
    hit = {"provision": "us-tcpa-rules/64.1200", "refs": ["(b)", "(c)", "(c)(1)"]}
    assert evals.matches(hit, "us-tcpa-rules/64.1200#(c)(1)")
    assert not evals.matches(hit, "us-tcpa-rules/64.1200#(d)(6)")
    assert evals.matches(hit, "us-tcpa-rules/64.1200")


def test_threshold_choices():
    scored = [(5.0, True), (3.0, True), (1.0, True), (-1.0, True), (-2.0, False), (-6.0, False)]
    assert evals._best_threshold(scored) == -1.5
    assert evals._floor(scored, keep=1.0) == -1.01


def test_eval_run_reports_every_variant(engine):
    r = evals.run("lagos-tenancy", engine.retriever("lagos-tenancy"))
    assert [v["key"] for v in r["variants"]] == ["bm25", "dense", "hybrid", "rerank", "rerank_filter"]
    assert r["thresholds"]["refuse_below"] is not None and r["variants"][-1]["refusal"]
