"""python -m cite_desk <command>

    fetch     download the official texts into corpus/raw (only needed to refresh the corpus)
    build     parse them into corpus/<collection>/*.jsonl (needs poppler's pdftotext for the PDFs)
    index     chunk, embed and index every collection into data/
    ask       ask a question from the command line: ask "Can I cold email a sole trader in the UK?"
    eval      run the retrieval evals and write evals/results.md
    serve     the demo app on http://127.0.0.1:8050
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main(argv=None):
    p = argparse.ArgumentParser(prog="cite_desk", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--only", nargs="*")
    sub.add_parser("build")
    i = sub.add_parser("index")
    i.add_argument("--collection", nargs="*")
    a = sub.add_parser("ask")
    a.add_argument("question")
    a.add_argument("--collection", default="outreach")
    a.add_argument("--mode", choices=["bm25", "dense", "hybrid", "rerank"])
    a.add_argument("--trace", action="store_true", help="print the retrieval trace as JSON")
    e = sub.add_parser("eval")
    e.add_argument("--collection", nargs="*")
    e.add_argument("--answers", action="store_true", help="also judge Claude's answers end to end (needs CITE_DESK_MODE=anthropic and a key)")
    s = sub.add_parser("serve")
    s.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8050")))
    args = p.parse_args(argv)

    if args.cmd == "fetch":
        from .corpus import fetch
        fetch(set(args.only) if args.only else None)
    elif args.cmd == "build":
        from .corpus import build
        build()
    elif args.cmd == "index":
        from . import index
        from .models import Embedder
        from .sources import COLLECTIONS
        e = Embedder()
        for c in args.collection or COLLECTIONS:
            index.build(c, e)
    elif args.cmd == "ask":
        from .answer import plain
        from .service import Engine
        eng = Engine()
        s = eng.search(args.collection, args.question, mode=args.mode)
        for n, h in enumerate(s["hits"], start=1):
            print(f"  {n}. {h['score']:8.3f}  {h['cite']}  {h['title'][:60]}")
        a = eng.answer(args.collection, args.question, s)
        from .answer import Answer, Citation
        obj = Answer(**{**a, "citations": [Citation(**c) for c in a["citations"]]})
        print("\n" + plain(obj))
        if args.trace:
            print(json.dumps(s["trace"], indent=2))
    elif args.cmd == "eval":
        from . import evals
        from .service import Engine
        from .sources import COLLECTIONS
        eng = Engine()
        results = []
        for c in args.collection or COLLECTIONS:
            last = [0]

            def prog(variant, done, total, c=c):
                pct = int(100 * done / total)
                if pct != last[0]:
                    last[0] = pct
                    print(f"\r{c}: {pct}% ({variant})", end="", file=sys.stderr, flush=True)

            results.append(evals.run(c, eng.retriever(c), progress=prog))
            print(file=sys.stderr)
        path = evals.save(results, eng.embedder.name, eng.reranker.name)
        print(path.read_text())
        if args.answers:
            from .answer import mode
            if mode() != "claude":
                print("Skipping answer evals: set CITE_DESK_MODE=anthropic and ANTHROPIC_API_KEY.")
            else:
                eng = Engine()  # picks up the thresholds just written
                lines = ["# Answer evals (Claude)", ""]
                for c in args.collection or COLLECTIONS:
                    r = evals.run_answers(c, eng)
                    lines += [f"## {c}", "", f"- Answerable questions answered: {r['answered']:.0%}",
                              f"- Citations pointing at a labelled provision: {r['citation_precision']:.0%}",
                              f"- Answers citing at least one labelled provision: {r['grounded_hit']:.0%}",
                              f"- Unanswerable questions refused: {r['refused_unanswerable']:.0%} "
                              f"({r['refused_by_gate']} by the gate, {r['refused_by_model']} by the model)",
                              f"- Cost: ${r['cost_usd']} for {len(r['rows'])} questions; median answer {r['p50_answer_ms']:.0f} ms", ""]
                (evals.EVALS / "answers.md").write_text("\n".join(lines) + "\n")
                print("\n".join(lines))
    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("cite_desk.web:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
