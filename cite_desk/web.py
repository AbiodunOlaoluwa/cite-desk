"""The demo app. `python -m cite_desk serve`, then open http://127.0.0.1:8050."""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import answer as answering
from . import evals as evaluation
from .corpus import manifest, provisions
from .service import AnswerCache, Engine, Jobs, Limits, eval_job, provision_detail
from .sources import COLLECTIONS

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app):
    # Load the models and indexes in the background so the page is up at once.
    if os.environ.get("CITE_DESK_WARM", "1") == "1":
        asyncio.get_running_loop().run_in_executor(None, ENGINE.warm)
    yield


app = FastAPI(title="Cite Desk", docs_url="/api/docs", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

ENGINE = Engine()
JOBS = Jobs()
LIMITS = Limits()
SEARCHES = AnswerCache(512)
ANSWERS = AnswerCache(256)


class Ask(BaseModel):
    collection: str = "outreach"
    q: str = Field(min_length=3, max_length=500)
    mode: str | None = None  # bm25 | dense | hybrid | rerank
    k: int | None = Field(default=None, ge=1, le=10)
    jurisdiction_filter: bool | None = None


class EvalRun(BaseModel):
    collections: list[str] | None = None


def _check(collection: str):
    if collection not in COLLECTIONS:
        raise HTTPException(404, "No such collection.")


def _search(body: Ask) -> dict:
    _check(body.collection)
    key = (body.collection, body.q.strip().lower(), body.mode, body.k, body.jurisdiction_filter)
    cached = SEARCHES.get(key)
    if cached:
        return cached
    result = ENGINE.search(body.collection, body.q.strip(), mode=body.mode, k=body.k, jurisdiction_filter=body.jurisdiction_filter)
    SEARCHES.put(key, result)
    return result


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


@app.get("/api/meta")
def meta():
    m = manifest()
    return {"mode": answering.mode(), "model": answering.MODEL if answering.mode() == "claude" else None,
            "collections": m["collections"], "thresholds": ENGINE.thresholds,
            "chunks": {c: len(r.index.chunks) for c, r in ENGINE.retrievers.items()},
            "ready": all(c in ENGINE.retrievers for c in COLLECTIONS)}


@app.post("/api/search")
def search(body: Ask):
    return _search(body)


@app.post("/api/answer")
def answer(body: Ask, request: Request):
    s = _search(body)
    key = (body.collection, body.q.strip().lower(), answering.mode(), tuple(h["id"] for h in s["hits"]), s["abstain"])
    cached = ANSWERS.get(key)
    if cached:
        return {**cached, "cached": True}
    if answering.mode() == "claude" and not s["abstain"]:
        blocked = LIMITS.take(request.client.host if request.client else "-")
        if blocked:
            raise HTTPException(429, blocked)
    a = ENGINE.answer(body.collection, body.q.strip(), s)
    ANSWERS.put(key, a)
    return a


@app.post("/api/ask")
def ask(body: Ask, request: Request):
    return {"search": _search(body), "answer": answer(body, request)}


@app.get("/api/collections/{collection}/docs/{doc}")
def doc(collection: str, doc: str):
    _check(collection)
    counts = {}
    for c in ENGINE.retriever(collection).index.chunks:
        counts[c["provision"]] = counts.get(c["provision"], 0) + 1
    rows = [{"id": p["id"], "label": p["label"], "title": p["title"], "kind": p["kind"], "path": p.get("path", ""),
             "words": sum(len(b["text"].split()) for b in p["blocks"]), "chunks": counts.get(p["id"], 0)}
            for p in provisions(collection) if p["doc"] == doc]
    if not rows:
        raise HTTPException(404, "No such document.")
    return rows


@app.get("/api/collections/{collection}/provision")
def provision(collection: str, id: str):
    _check(collection)
    d = provision_detail(ENGINE, collection, id)
    if not d:
        raise HTTPException(404, "No such provision.")
    return d


@app.get("/api/collections/{collection}/questions")
def eval_questions(collection: str):
    _check(collection)
    return evaluation.questions(collection)


@app.get("/api/evals")
def evals_results():
    path = evaluation.EVALS / "results.json"
    return json.loads(path.read_text()) if path.exists() else {"collections": []}


@app.post("/api/jobs/eval")
def run_evals(body: EvalRun):
    cols = body.collections or list(COLLECTIONS)
    for c in cols:
        _check(c)
    if not JOBS.start("eval", eval_job(ENGINE, cols)):
        raise HTTPException(409, "The evals are already running.")
    return JOBS.snapshot()


@app.get("/api/job")
def job():
    return JOBS.snapshot()


@app.get("/api/job/stream")
async def job_stream(request: Request):
    """Server-sent events: the eval job's real position, pushed the moment it changes."""

    async def gen():
        version = -1
        while not await request.is_disconnected():
            version, snap = await asyncio.to_thread(JOBS.wait, version, 10.0)
            yield f"data: {json.dumps(snap)}\n\n"
            await asyncio.sleep(0.05)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})
