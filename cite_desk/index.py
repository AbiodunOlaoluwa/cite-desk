"""The search index for one collection: SQLite holding the chunks, an FTS5 keyword index, and one vector per chunk.

No vector database: about 800 vectors of 384 numbers is 1.2 MB, and comparing a question against all of them with
numpy takes well under a millisecond. The index is rebuilt automatically when the corpus, the chunker or the
embedding model changes (a fingerprint of all three is stored with it).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import chunk as chunking
from .corpus import CORPUS, provisions
from .sources import COLLECTIONS, DOCS

DATA = Path(__file__).resolve().parent.parent / "data"

SCHEMA = """
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE chunks (
  rowid INTEGER PRIMARY KEY, id TEXT UNIQUE, provision TEXT, doc TEXT, jurisdiction TEXT, label TEXT, title TEXT,
  path TEXT, url TEXT, kind TEXT, cite TEXT, refs TEXT, blocks TEXT, context TEXT, header TEXT, text TEXT, words INT
);
CREATE VIRTUAL TABLE chunks_fts USING fts5(header, text, content='chunks', content_rowid='rowid',
  tokenize='porter unicode61 remove_diacritics 2');
CREATE TABLE vectors (rowid INTEGER PRIMARY KEY, v BLOB);
"""


def fingerprint(collection: str, embed_name: str) -> str:
    h = hashlib.sha256()
    for doc in COLLECTIONS[collection].docs:
        h.update((CORPUS / collection / f"{doc.id}.jsonl").read_bytes())
    h.update(Path(chunking.__file__).read_bytes())
    h.update(embed_name.encode())
    return h.hexdigest()[:16]


@dataclass
class Index:
    collection: str
    conn: sqlite3.Connection
    chunks: list[dict]  # in rowid order
    by_rowid: dict[int, dict]
    by_id: dict[str, dict]
    matrix: np.ndarray  # one normalised row per chunk, same order as `chunks`
    rowids: np.ndarray
    jurisdictions: np.ndarray
    built_seconds: float = 0.0

    def vector_rows(self, allowed: set[str] | None) -> np.ndarray:
        if not allowed:
            return np.ones(len(self.chunks), dtype=bool)
        return np.isin(self.jurisdictions, list(allowed))


def build(collection: str, embedder, path: Path | None = None, log=print) -> Path:
    path = path or DATA / f"{collection}.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    t0 = time.time()
    conn = sqlite3.connect(tmp)
    conn.executescript(SCHEMA)
    rows = []
    for p in provisions(collection):
        doc = DOCS[p["doc"]]
        for c in chunking.chunk_provision(p, doc.title):
            rows.append(c)
    log(f"{collection}: {len(rows)} chunks; embedding with {embedder.name}")
    texts = [f"{c.header}\n{c.context}\n{c.text}".strip() for c in rows]
    vectors = embedder.passages(texts)
    for i, c in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (i, c.id, c.provision, c.doc, DOCS[c.doc].jurisdiction, c.label, c.title, c.path, c.url, c.kind, c.cite,
             json.dumps(c.refs), json.dumps(c.blocks), c.context, c.header, c.text, c.words),
        )
        conn.execute("INSERT INTO vectors VALUES (?,?)", (i, vectors[i - 1].astype(np.float32).tobytes()))
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
    meta = {"fingerprint": fingerprint(collection, embedder.name), "embed_model": embedder.name, "chunks": str(len(rows)),
            "dims": str(vectors.shape[1]), "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "build_seconds": f"{time.time() - t0:.1f}"}
    conn.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    conn.commit()
    conn.close()
    tmp.replace(path)
    log(f"{collection}: index built in {time.time() - t0:.1f}s -> {path}")
    return path


def load(collection: str, embedder, path: Path | None = None, rebuild_if_stale: bool = True, log=print) -> Index:
    path = path or DATA / f"{collection}.sqlite"
    fp = fingerprint(collection, embedder.name)
    if path.exists():
        conn = sqlite3.connect(path, check_same_thread=False)
        stored = dict(conn.execute("SELECT k, v FROM meta").fetchall())
        if stored.get("fingerprint") != fp and rebuild_if_stale:
            conn.close()
            build(collection, embedder, path, log)
    else:
        build(collection, embedder, path, log)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    chunks, vecs, rowids, jur = [], [], [], []
    for r in conn.execute("SELECT c.*, v.v AS vec FROM chunks c JOIN vectors v ON v.rowid = c.rowid ORDER BY c.rowid"):
        d = {k: r[k] for k in r.keys() if k != "vec"}
        d["refs"], d["blocks"] = json.loads(d["refs"]), json.loads(d["blocks"])
        chunks.append(d)
        vecs.append(np.frombuffer(r["vec"], dtype=np.float32))
        rowids.append(r["rowid"])
        jur.append(r["jurisdiction"])
    meta = dict(conn.execute("SELECT k, v FROM meta").fetchall())
    return Index(collection, conn, chunks, {c["rowid"]: c for c in chunks}, {c["id"]: c for c in chunks}, np.vstack(vecs), np.array(rowids),
                 np.array(jur), float(meta.get("build_seconds", 0)))
