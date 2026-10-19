"""Fetch the official texts, parse them into provisions, and load the committed corpus.

    python -m cite_desk fetch   # download into corpus/raw (gitignored), politely
    python -m cite_desk build   # parse into corpus/<collection>/<doc>.jsonl + corpus/manifest.json (committed)
"""
from __future__ import annotations

import gzip
import hashlib
import json
import time
import urllib.request
from datetime import date
from pathlib import Path

from .parsers import ecfr, eurlex, guides, legislation, pdflaw, uscode
from .sources import COLLECTIONS, DOCS, Doc

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
RAW = CORPUS / "raw"
UA = "cite-desk-corpus/0.1 (+https://github.com/AbiodunOlaoluwa/cite-desk)"
DELAY = {"www.legislation.gov.uk": 5.0}  # robots.txt Crawl-delay

PARSERS = {
    "uscode": uscode.parse,
    "ecfr": ecfr.parse,
    "ftc_guide": guides.parse_ftc,
    "eurlex_oj": eurlex.parse_oj,
    "eurlex_consolidated": eurlex.parse_consolidated,
    "legislation_uk": legislation.parse_uk,
    "justice_ca": legislation.parse_ca,
    "ndpc_gaid": pdflaw.parse_gaid,
    "lagos_law": pdflaw.parse_lagos,
}
BINARY = {"ndpc_gaid", "lagos_law", "legislation_uk", "justice_ca", "ecfr"}


def _get(url: str, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", **headers})
    with urllib.request.urlopen(req, timeout=90) as r:
        body = r.read()
        return gzip.decompress(body) if r.headers.get("Content-Encoding") == "gzip" else body


def _ecfr_date() -> str:
    titles = json.loads(_get("https://www.ecfr.gov/api/versioner/v1/titles.json", {}))
    return min(t["up_to_date_as_of"] for t in titles["titles"] if t["number"] in (16, 47))


def raw_path(doc: Doc) -> Path:
    return RAW / f"{doc.id}.{doc.ext}"


def fetch(only: set[str] | None = None, log=print) -> dict:
    RAW.mkdir(parents=True, exist_ok=True)
    ecfr_date = None
    fetched = {}
    last_host = {}
    for doc in DOCS.values():
        if only and doc.id not in only:
            continue
        url = doc.fetch_url
        if "{ecfr_date}" in url:
            ecfr_date = ecfr_date or _ecfr_date()
            url = url.format(ecfr_date=ecfr_date)
        host = url.split("/")[2]
        wait = DELAY.get(host, 1.5) - (time.time() - last_host.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        body = _get(url, doc.headers)
        last_host[host] = time.time()
        raw_path(doc).write_bytes(body)
        fetched[doc.id] = {"url": url, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                           "retrieved": date.today().isoformat()}
        log(f"fetched {doc.id}: {len(body):,} bytes")
    meta = CORPUS / "fetched.json"
    old = json.loads(meta.read_text()) if meta.exists() else {}
    old.update(fetched)
    meta.write_text(json.dumps(old, indent=2, sort_keys=True) + "\n")
    return fetched


def build(log=print) -> dict:
    fetched = json.loads((CORPUS / "fetched.json").read_text())
    manifest = {"collections": {}}
    for col in COLLECTIONS.values():
        (CORPUS / col.id).mkdir(parents=True, exist_ok=True)
        docs = []
        for doc in col.docs:
            raw = raw_path(doc).read_bytes()
            data = raw if doc.parser in BINARY else raw.decode("utf-8", errors="replace")
            provs = [p.to_json() for p in PARSERS[doc.parser](data, doc)]
            path = CORPUS / col.id / f"{doc.id}.jsonl"
            path.write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in provs))
            words = sum(len(b["text"].split()) for p in provs for b in p["blocks"])
            f = fetched.get(doc.id, {})
            docs.append({"id": doc.id, "title": doc.title, "short": doc.short, "jurisdiction": doc.jurisdiction,
                         "kind": doc.kind, "url": doc.home_url, "source": f.get("url", doc.fetch_url),
                         "retrieved": f.get("retrieved"), "sha256": f.get("sha256"), "licence": doc.licence,
                         "note": doc.note, "provisions": len(provs), "words": words})
            log(f"built {doc.id}: {len(provs)} provisions, {words:,} words")
        manifest["collections"][col.id] = {"title": col.title, "blurb": col.blurb, "examples": list(col.examples), "docs": docs}
    (CORPUS / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest


def manifest() -> dict:
    return json.loads((CORPUS / "manifest.json").read_text())


def provisions(collection: str) -> list[dict]:
    out = []
    for doc in COLLECTIONS[collection].docs:
        path = CORPUS / collection / f"{doc.id}.jsonl"
        out += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return out
