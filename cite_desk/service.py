"""What the web app and the command line share: loaded models, one retriever per collection, answers, evals."""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from . import answer as answering
from . import evals as evaluation
from . import index as indexing
from .corpus import manifest, provisions
from .models import Embedder, Reranker
from .retrieve import Retriever, Settings
from .sources import COLLECTIONS

DAILY_CAP = int(os.environ.get("CITE_DESK_DAILY_CAP", "300"))  # model calls per UTC day, across everyone
PER_IP_HOURLY = int(os.environ.get("CITE_DESK_PER_IP_HOURLY", "20"))


class Engine:
    """Loads the two models once and an index per collection, on first use."""

    def __init__(self, embedder=None, reranker=None, data_dir=None):
        self._lock = threading.Lock()
        self.embedder, self.reranker = embedder, reranker
        self.data_dir = data_dir or indexing.DATA
        self.retrievers: dict[str, Retriever] = {}
        self.thresholds = self._thresholds()
        self.gold = {c: {q["q"].strip().lower(): q for q in evaluation.questions(c)} for c in COLLECTIONS
                     if (evaluation.EVALS / f"{c}.jsonl").exists()}

    @staticmethod
    def _thresholds() -> dict:
        path = evaluation.EVALS / "thresholds.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def retriever(self, collection: str) -> Retriever:
        if collection not in COLLECTIONS:
            raise KeyError(collection)
        with self._lock:
            if self.embedder is None:
                self.embedder = Embedder()
            if self.reranker is None:
                self.reranker = Reranker()
            if collection not in self.retrievers:
                path = self.data_dir / f"{collection}.sqlite"
                self.retrievers[collection] = Retriever(indexing.load(collection, self.embedder, path), self.embedder, self.reranker)
            return self.retrievers[collection]

    def warm(self):
        for c in COLLECTIONS:
            self.retriever(c)

    def settings(self, collection: str, **over) -> Settings:
        t = self.thresholds.get(collection) or {}
        if not isinstance(t, dict):  # an older results file held a single line
            t = {"weak_below": t}
        s = Settings(refuse_below=t.get("refuse_below"), weak_below=t.get("weak_below"))
        for k, v in over.items():
            if v is not None and hasattr(s, k):
                setattr(s, k, v)
        return s

    def search(self, collection: str, q: str, **over) -> dict:
        r = self.retriever(collection).search(q, self.settings(collection, **over))
        gold = self.gold.get(collection, {}).get(q.strip().lower())
        hits = [{k: v for k, v in h.items() if k != "rowid"} for h in r.hits]
        if gold:
            for h in hits:
                h["gold"] = any(evaluation.matches(h, g) for g in gold["gold"])
            by_id = self.retriever(collection).index.by_id
            for name in ("bm25", "dense", "fused", "reranked"):
                for item in r.trace.get(name, []):
                    item["gold"] = any(evaluation.matches(by_id[item["id"]], g) for g in gold["gold"])
        return {"question": q, "collection": collection, "hits": hits, "trace": r.trace, "abstain": r.abstain, "weak": r.weak,
                "eval": {"id": gold["id"], "gold": gold["gold"], "answerable": gold["answerable"]} if gold else None}

    def answer(self, collection: str, q: str, search: dict, client=None) -> dict:
        if search["abstain"]:
            a = answering.refusal(collection, search["trace"], "the best passage scored below the refusal threshold")
            a.mode = answering.mode()
        elif answering.mode() == "claude":
            a = answering.answer_claude(q, search["hits"], collection, client=client)
        else:
            a = answering.answer_extractive(q, search["hits"], self.reranker, weak=search.get("weak", False))
        return a.to_json()


@dataclass
class Limits:
    """Spending guards for the model: a daily cap for everyone and an hourly allowance per visitor."""

    day: str = ""
    used_today: int = 0
    per_ip: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def take(self, ip: str) -> str | None:
        now = time.time()
        with self.lock:
            today = time.strftime("%Y-%m-%d", time.gmtime(now))
            if today != self.day:
                self.day, self.used_today = today, 0
            if self.used_today >= DAILY_CAP:
                return "The daily allowance of model answers is used up. Search still works; answers resume tomorrow (UTC)."
            q = self.per_ip.setdefault(ip, deque())
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= PER_IP_HOURLY:
                return "That's the hourly limit for model answers from one visitor. Search still works."
            q.append(now)
            self.used_today += 1
            return None


class AnswerCache:
    def __init__(self, size: int = 256):
        self.size, self.data = size, OrderedDict()

    def get(self, key):
        if key in self.data:
            self.data.move_to_end(key)
            return self.data[key]
        return None

    def put(self, key, value):
        self.data[key] = value
        self.data.move_to_end(key)
        while len(self.data) > self.size:
            self.data.popitem(last=False)


class Jobs:
    """One background job at a time (the evals). Every change bumps a version and wakes waiting streams, so the
    progress bar shows the evaluator's real position: it never runs ahead and never lags."""

    def __init__(self):
        self.cond = threading.Condition()
        self.version = 0
        self.state = {"kind": None, "state": "idle", "done": 0, "total": 0, "phase": "", "message": "", "result": None,
                      "started": None, "finished": None}

    def _bump(self):
        self.version += 1
        self.cond.notify_all()

    def snapshot(self) -> dict:
        with self.cond:
            return {**self.state, "version": self.version}

    def wait(self, last: int, timeout: float) -> tuple[int, dict]:
        with self.cond:
            self.cond.wait_for(lambda: self.version != last, timeout=timeout)
            return self.version, {**self.state, "version": self.version}

    def busy(self) -> bool:
        return self.state["state"] == "running"

    def start(self, kind: str, fn) -> bool:
        with self.cond:
            if self.busy():
                return False
            self.state = {"kind": kind, "state": "running", "done": 0, "total": 0, "phase": "", "message": "",
                          "result": None, "started": time.time(), "finished": None}
            self._bump()

        def target():
            try:
                result = fn(self)
                with self.cond:
                    self.state.update(state="done", result=result, finished=time.time())
                    self._bump()
            except Exception as e:  # shown in the UI rather than lost in a thread
                traceback.print_exc()
                with self.cond:
                    self.state.update(state="failed", message=f"{type(e).__name__}: {e}", finished=time.time())
                    self._bump()

        threading.Thread(target=target, daemon=True).start()
        return True

    def progress(self, phase: str, done: int, total: int):
        with self.cond:
            self.state.update(phase=phase, done=done, total=total)
            self._bump()


def eval_job(engine: Engine, collections: list[str]):
    def run(jobs: Jobs):
        results = []
        per = {c: len(evaluation.questions(c)) * len(evaluation.VARIANTS) for c in collections}
        total, base = sum(per.values()), 0
        for c in collections:
            def prog(variant, done, _t, c=c, base=base):
                jobs.progress(f"{COLLECTIONS[c].title}: {dict((k, l) for k, l, _ in evaluation.VARIANTS)[variant]}", base + done, total)
            results.append(evaluation.run(c, engine.retriever(c), progress=prog))
            base += per[c]
        existing = json.loads((evaluation.EVALS / "results.json").read_text())["collections"] if (evaluation.EVALS / "results.json").exists() else []
        merged = {r["collection"]: r for r in existing}
        merged.update({r["collection"]: r for r in results})
        evaluation.save([merged[c] for c in COLLECTIONS if c in merged], engine.embedder.name, engine.reranker.name)
        engine.thresholds = engine._thresholds()
        return {"collections": collections}
    return run


def provision_detail(engine: Engine, collection: str, pid: str) -> dict | None:
    p = next((x for x in provisions(collection) if x["id"] == pid), None)
    if not p:
        return None
    chunks = [c for c in engine.retriever(collection).index.chunks if c["provision"] == pid]
    return {"provision": p, "chunks": [{"id": c["id"], "cite": c["cite"], "words": c["words"], "refs": c["refs"],
                                        "context": c["context"], "blocks": c["blocks"]} for c in chunks]}
