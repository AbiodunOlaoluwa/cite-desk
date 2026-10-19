/* Cite Desk: vanilla JS, no build. Every number on screen comes from the server's own trace. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtMs = (ms) => (ms == null ? "–" : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`);
const pct = (x) => `${Math.round((x || 0) * 100)}%`;
const PLACE = { US: "US", EU: "EU", UK: "UK", CA: "Canada", NG: "Nigeria", "NG-LA": "Lagos" };

const state = {
  meta: null,
  collection: localGet("cd.collection") || "outreach",
  tab: "ask",
  runs: {}, // per collection: { q, search, answer, phase, error }
  hood: {}, // per collection: a search re-run with other settings, for comparing
  questions: {},
  evals: null,
  job: null,
  corpus: { doc: null, provs: null, prov: null, detail: null },
};

function localGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) } : {});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3200);
}

/* ---------- shell ---------- */
async function loadMeta() {
  state.meta = await api("/api/meta");
  const mode = $("#mode");
  if (state.meta.mode === "claude") { mode.textContent = `Claude · ${state.meta.model}`; mode.className = "mode claude"; }
  else { mode.textContent = "Extractive · no model"; mode.className = "mode"; }
  const cols = state.meta.collections;
  if (!cols[state.collection]) state.collection = Object.keys(cols)[0];
  $("#colpick").innerHTML = Object.entries(cols).map(([id, c]) =>
    `<button type="button" data-col="${esc(id)}" class="${id === state.collection ? "on" : ""}">${esc(c.title)}</button>`).join("");
}

function col() { return state.meta.collections[state.collection]; }
function run() { return (state.runs[state.collection] ||= {}); }

function setTab() {
  const tab = (location.hash || "#ask").slice(1).split("?")[0];
  state.tab = ["ask", "hood", "corpus", "evals"].includes(tab) ? tab : "ask";
  $$(".tabs a").forEach((a) => a.classList.toggle("on", a.dataset.tab === state.tab));
  render();
}

function render() {
  const view = $("#view");
  if (!state.meta) { view.innerHTML = `<div class="status idle"><span class="pulse"></span>Loading…</div>`; return; }
  ({ ask: renderAsk, hood: renderHood, corpus: renderCorpus, evals: renderEvals })[state.tab](view);
}

document.addEventListener("click", (e) => {
  const colBtn = e.target.closest("[data-col]");
  if (colBtn) {
    state.collection = colBtn.dataset.col;
    localSet("cd.collection", state.collection);
    state.corpus = { doc: null, provs: null, prov: null, detail: null };
    $$("#colpick button").forEach((b) => b.classList.toggle("on", b.dataset.col === state.collection));
    render();
  }
});

/* ---------- ask ---------- */
function renderAsk(view) {
  const c = col();
  const r = run();
  const docs = c.docs.length;
  const places = new Set(c.docs.map((d) => d.jurisdiction)).size;
  const passages = state.meta.chunks?.[state.collection];
  view.innerHTML = `
    <section class="ask-hero">
      <div class="run-intro">
        <h1>Ask the law. Get the <em>paragraph</em> it came from.</h1>
        <p class="lede">${esc(c.blurb)} Answers come only from these official texts, each point tied to the paragraph it rests on. When the texts don't cover a question, it says so instead of guessing.</p>
        <div class="facts">
          <span class="fact">${docs} document${docs > 1 ? "s" : ""}</span>
          <span class="fact">${places} place${places > 1 ? "s" : ""}</span>
          ${passages ? `<span class="fact">${passages.toLocaleString()} passages</span>` : ""}
          <span class="fact">search runs on this server</span>
        </div>
      </div>
      <div class="ask-box">
        <form class="ask-row" id="ask-form">
          <label class="visually-hidden" for="q" hidden>Your question</label>
          <input id="q" name="q" autocomplete="off" maxlength="500" placeholder="Ask about ${esc(c.title.toLowerCase())}…" value="${esc(r.q || "")}">
          <button class="btn btn-accent" type="submit" id="ask-btn">Ask</button>
        </form>
        <div class="ask-under">
          <div class="examples"><span class="small muted">Try</span>${c.examples.slice(0, 4).map((x) => `<button type="button" class="example" data-q="${esc(x)}">${esc(x)}</button>`).join("")}</div>
          <div class="phase-line" id="phase" aria-live="polite">${phaseHTML(r)}</div>
        </div>
      </div>
    </section>
    <section id="result">${resultHTML(r)}</section>
    <p class="disclaimer">Not legal advice. Texts as retrieved from the official sources listed under Corpus; check the source before relying on any of it.</p>`;
  $("#ask-form").addEventListener("submit", (e) => { e.preventDefault(); ask($("#q").value); });
  $$(".example").forEach((b) => b.addEventListener("click", () => { $("#q").value = b.dataset.q; ask(b.dataset.q); }));
  wireCites();
}

function phaseHTML(r) {
  if (!r.phase) return "";
  const s = r.search;
  const steps = [];
  steps.push(r.phase === "searching"
    ? `<span class="step now"><span class="dot"></span>Searching ${esc(col().docs.length)} documents</span>`
    : `<span class="step done"><span class="dot"></span>Found ${s ? s.hits.length : 0} passages in ${fmtMs(sumMs(s))}</span>`);
  if (r.phase === "answering") {
    steps.push(`<span class="step now"><span class="dot"></span>${state.meta.mode === "claude" ? "Claude is writing a cited answer" : "Choosing the paragraphs to quote"}</span>`);
  } else if (r.phase === "done" && r.answer) {
    steps.push(`<span class="step done"><span class="dot"></span>${r.answer.status === "refused" ? "Refused: not covered" : `Answered in ${fmtMs(r.answer.ms)}`}</span>`);
  } else if (r.phase === "error") {
    steps.push(`<span class="step" style="color:var(--red)"><span class="dot" style="background:var(--red-dot)"></span>${esc(r.error)}</span>`);
  }
  return steps.join("");
}

const sumMs = (s) => (s ? Object.values(s.trace.ms || {}).reduce((a, b) => a + b, 0) : 0);

async function ask(q) {
  q = (q || "").trim();
  if (q.length < 3) { toast("Ask a question first."); return; }
  const key = state.collection;
  const r = (state.runs[key] = { q, phase: "searching" });
  delete state.hood[key];
  const update = () => { if (state.tab === "ask" && state.collection === key) { $("#phase").innerHTML = phaseHTML(r); $("#result").innerHTML = resultHTML(r); wireCites(); } };
  $("#ask-btn").disabled = true;
  update();
  try {
    r.search = await api("/api/search", { collection: key, q });
    r.phase = "answering";
    update();
    r.answer = await api("/api/answer", { collection: key, q });
    r.phase = "done";
  } catch (err) {
    r.phase = "error";
    r.error = err.message;
  }
  if ($("#ask-btn")) $("#ask-btn").disabled = false;
  update();
}

function resultHTML(r) {
  if (!r.search || !r.answer) return "";
  const a = r.answer;
  const s = r.search;
  const refused = a.status === "refused";
  const weak = a.status === "weak";
  const cited = new Set(a.citations.map((c) => c.chunk_id));
  const body = a.segments.map((seg) => {
    const chips = seg.cites.map((n) => `<button type="button" class="cite" data-n="${n}" aria-label="Source ${n}">${n}</button>`).join("");
    if (seg.quote) return `<blockquote>${esc(seg.text)} ${chips}</blockquote>`;
    return esc(seg.text).replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br>") + chips;
  }).join("");
  const usage = a.usage && a.usage.output_tokens ? ` · ${a.usage.input_tokens.toLocaleString()} in / ${a.usage.output_tokens.toLocaleString()} out tokens · $${(a.usage.cost_micros / 1e6).toFixed(4)}` : "";
  const gate = s.trace.abstain;
  const line = s.abstain ? gate.refuse_below : gate.weak_below;
  const why = (refused || weak) && a.reason ? `<p class="small" style="color:#5d3a05">Why: ${esc(a.reason)}${gate && gate.top_score != null && line != null && (s.abstain || s.weak) ? ` (best passage ${gate.top_score.toFixed(2)}; line ${line.toFixed(2)})` : ""}.</p>` : "";
  const others = s.hits.filter((h) => !cited.has(h.id));
  return `
    <div class="result-grid">
      <article class="answer-card ${refused || weak ? "refused" : ""}">
        <div class="answer-head">
          ${refused ? `<span class="chip c-held">Not in these documents</span>` : weak ? `<span class="chip c-held">Weak match: may not answer this</span>` : `<span class="chip c-ok">Answered from ${a.citations.length} passage${a.citations.length === 1 ? "" : "s"}</span>`}
          <span class="chip ${a.mode === "claude" ? "c-ok" : "c-info"}">${a.mode === "claude" ? esc(a.model || "Claude") : "extractive"}</span>
        </div>
        <p class="answer-q">${esc(r.q)}</p>
        <div class="answer-body"><p>${body}</p></div>
        ${why}
        <div class="answer-foot">
          <span>search ${fmtMs(sumMs(s))}</span><span>answer ${fmtMs(a.ms)}${usage}</span>
          ${s.trace.query.filtered_to.length ? `<span>searched ${esc(s.trace.query.filtered_to.map((j) => PLACE[j] || j).join(", "))} only</span>` : ""}
          <a href="#hood">How it found these →</a>
        </div>
      </article>
      <aside class="sources">
        ${a.citations.map((c) => `
          <div class="source" id="src-${c.n}">
            <div class="source-top"><span class="source-n">${c.n}</span><div><div class="source-cite">${esc(c.cite)}</div><div class="source-title">${esc(c.title)}</div></div></div>
            <q>${esc(c.quote)}</q>
            <div class="source-links"><span class="chip c-muted">${esc(PLACE[c.jurisdiction] || c.jurisdiction)}</span><a href="${esc(c.url)}" target="_blank" rel="noopener">Official text ↗</a></div>
          </div>`).join("")}
        ${others.length ? `<div class="card"><p class="label">${refused ? "Closest passages it found" : "Also retrieved, not quoted"}</p><ul class="also">${others.map((h) => `
          <li><span><span class="mono">${esc(h.cite)}</span><span class="sub small muted"> ${esc(h.title)}</span></span><span class="score">${h.score.toFixed(2)}</span></li>`).join("")}</ul></div>` : ""}
      </aside>
    </div>`;
}

function wireCites() {
  $$(".cite").forEach((b) => {
    const src = () => $(`#src-${b.dataset.n}`);
    b.addEventListener("mouseenter", () => src()?.classList.add("on"));
    b.addEventListener("mouseleave", () => src()?.classList.remove("on"));
    b.addEventListener("click", () => { const el = src(); if (el) { el.scrollIntoView({ behavior: "smooth", block: "nearest" }); el.classList.add("on"); setTimeout(() => el.classList.remove("on"), 1400); } });
  });
}

/* ---------- under the hood ---------- */
async function loadQuestions() {
  if (!state.questions[state.collection]) state.questions[state.collection] = await api(`/api/collections/${state.collection}/questions`);
  return state.questions[state.collection];
}

function renderHood(view) {
  const r = run();
  const s = state.hood[state.collection] || r.search;
  if (!s) {
    view.innerHTML = `
      <div class="section-head"><h2>Under the hood</h2></div>
      <p class="lede">Ask a question and this page shows every step: what each search found, how they were combined, how the re-ranker reordered them, and whether the answer cleared the bar to be given at all.</p>
      <div class="card controls-row"><select class="pick-q" id="pick-q"><option value="">Pick a question from the eval set…</option></select>
      <a class="btn btn-ghost btn-sm" href="#ask">Or ask your own</a></div>`;
    fillPicker();
    return;
  }
  const t = s.trace;
  const ev = s.eval;
  const set = t.settings;
  const ms = t.ms || {};
  const gate = t.abstain || {};
  const stages = [
    { n: 1, h: "Read the question", p: `${t.query.terms.length} search terms${t.query.jurisdictions.length ? `; names ${t.query.jurisdictions.map((j) => PLACE[j] || j).join(", ")}` : ""}`, ms: null },
    { n: 2, h: "Keyword search", p: "BM25 over every passage, title weighted double", ms: ms.bm25, skip: !("bm25" in ms) },
    { n: 3, h: "Meaning search", p: "The question as a vector, compared with all passages", ms: ms.dense, skip: !("dense" in ms) },
    { n: 4, h: "Fuse", p: "Reciprocal rank fusion: agreement between the two lists wins", ms: ms.fuse, skip: !("fuse" in ms) },
    { n: 5, h: "Re-rank", p: "A cross-encoder reads the question with each of the top 24", ms: ms.rerank, skip: !("rerank" in ms) },
    { n: 6, h: "Answer or refuse", p: gate.refuse_below == null || !("rerank" in ms) ? "Only judged after re-ranking" : `Best ${gate.top_score?.toFixed(2)}; refuse below ${gate.refuse_below.toFixed(2)}, weak below ${gate.weak_below.toFixed(2)}`, ms: null, held: gate.abstain || gate.weak, verdict: gate.abstain ? "refuse" : gate.weak ? "weak match" : "answer" },
  ];
  const fusedRank = Object.fromEntries((t.fused || []).map((x, i) => [x.id, i + 1]));
  const lists = [
    ["Keyword (BM25)", t.bm25, "bm25 score"],
    ["Meaning (embeddings)", t.dense, "cosine"],
    ["Fused (RRF)", t.fused, "rrf"],
    ["Re-ranked", t.reranked, "cross-encoder"],
  ];
  const kept = new Set(s.hits.map((h) => h.id));
  const evalNote = ev ? `<div class="eval-note"><span class="chip c-info">eval ${esc(ev.id)}</span>${ev.answerable
      ? `The labelled answer is ${ev.gold.map((g) => `<span class="mono">${esc(g)}</span>`).join(", ")}. Green marks it in each list.`
      : "This question is labelled unanswerable from these documents: the right move is to refuse."}</div>` : "";
  view.innerHTML = `
    <div class="hood-q">
      <span class="label">Under the hood</span>
      <h2>${esc(s.question)}</h2>
      ${evalNote}
      <div class="controls-row">
        <div class="segmented" id="mode-pick" role="group" aria-label="Search mode">
          ${[["bm25", "Keyword"], ["dense", "Meaning"], ["hybrid", "Hybrid"], ["rerank", "Hybrid + re-rank"]].map(([k, l]) => `<button type="button" data-mode="${k}" class="${set.mode === k ? "on" : ""}">${l}</button>`).join("")}
        </div>
        <label class="toggle"><input type="checkbox" id="jf" ${set.jurisdiction_filter ? "checked" : ""}> Search only the places the question names</label>
        <select class="pick-q" id="pick-q"><option value="">Another eval question…</option></select>
      </div>
    </div>
    <section class="pipeline">${stages.map((st) => `
      <div class="stage ${st.skip ? "skip" : ""} ${st.held ? "held" : ""}">
        <span class="idx">${String(st.n).padStart(2, "0")}</span><h3>${st.h}</h3><p>${esc(st.p)}</p>
        <span class="ms">${st.skip ? "skipped" : st.ms != null ? fmtMs(st.ms) : st.verdict && "rerank" in ms ? st.verdict : ""}</span>
      </div>`).join("")}
    </section>
    ${t.query.terms.length ? `<div class="facts"><span class="label" style="align-self:center;margin-right:6px">BM25 terms</span>${t.query.terms.map((x) => `<span class="fact">${esc(x)}</span>`).join("")}${t.query.filtered_to.length ? `<span class="fact fact-held">filtered to ${esc(t.query.filtered_to.map((j) => PLACE[j] || j).join(", "))}</span>` : ""}</div>` : ""}
    <section class="lists">${lists.map(([title, items, unit]) => `
      <div class="rank-col">
        <h3>${title}<small>${unit}</small></h3>
        ${items && items.length ? `<ol>${items.slice(0, 8).map((it, i) => {
          const from = title === "Re-ranked" && fusedRank[it.id] ? fusedRank[it.id] : null;
          const move = from ? (from > i + 1 ? `<span class="move up">from #${from}</span>` : from < i + 1 ? `<span class="move down">from #${from}</span>` : `<span>held #${from}</span>`) : "";
          return `<li class="rank-item ${it.gold ? "gold" : ""} ${kept.has(it.id) ? "kept" : ""}" data-id="${esc(it.id)}"><span class="r">${i + 1}</span><span><span class="c">${esc(it.cite)}</span><span class="t">${esc(it.title || "")}</span><span class="meta"><span>${it.score.toFixed(unit === "rrf" ? 4 : 2)}</span>${move}</span></span></li>`;
        }).join("")}</ol>` : `<p class="small muted">Not run in this mode.</p>`}
      </div>`).join("")}
    </section>
    ${gateHTML(gate)}
    ${shownHTML(s, run())}
    <details class="card"><summary class="label" style="cursor:pointer;min-height:40px;display:flex;align-items:center">The raw trace</summary><pre class="json">${esc(JSON.stringify(t, null, 2))}</pre></details>`;

  $$("#mode-pick button").forEach((b) => b.addEventListener("click", () => rerunHood({ mode: b.dataset.mode })));
  $("#jf").addEventListener("change", (e) => rerunHood({ jurisdiction_filter: e.target.checked }));
  $$(".rank-item").forEach((li) => {
    li.addEventListener("mouseenter", () => $$(`.rank-item[data-id="${CSS.escape(li.dataset.id)}"]`).forEach((x) => x.classList.add("hl")));
    li.addEventListener("mouseleave", () => $$(".rank-item.hl").forEach((x) => x.classList.remove("hl")));
  });
  fillPicker();
}

function gateHTML(g) {
  if (g.refuse_below == null || g.top_score == null) return "";
  const lo = Math.min(-12, g.top_score - 1, g.refuse_below - 1), hi = Math.max(10, g.top_score + 1);
  const at = (v) => `${((v - lo) / (hi - lo)) * 100}%`;
  const verdict = g.abstain ? ["c-held", "refuse"] : g.weak ? ["c-held", "weak match"] : ["c-ok", "answer"];
  return `
    <section class="card gate">
      <div class="section-head"><h3>Answer or refuse</h3><span class="chip ${verdict[0]}">${verdict[1]}</span></div>
      <p class="small muted">The re-ranker's score for the best passage is the signal. Below the floor the app refuses before any model is called. Between the floor and the weak line it answers but says the match is weak; with Claude on, the model reads the passages and can still refuse. Both lines are chosen on the dev half of the eval set.</p>
      <div class="gate-wrap">
        <div class="gate-bar">
          <div class="gate-zone" style="left:0;width:${at(g.refuse_below)}"></div>
          <div class="gate-mark th" style="left:${at(g.refuse_below)}"><span>floor ${g.refuse_below.toFixed(2)}</span></div>
          <div class="gate-mark wk" style="left:${at(g.weak_below)}"><span>weak line ${g.weak_below.toFixed(2)}</span></div>
          <div class="gate-mark best" style="left:${at(g.top_score)}"><span>best passage ${g.top_score.toFixed(2)}</span></div>
        </div>
      </div>
      <div class="gate-legend"><span>less relevant</span><span>more relevant</span></div>
    </section>`;
}

function shownHTML(s, r) {
  if (!r.answer || r.search !== s) return "";
  const a = r.answer;
  const quotes = a.citations.map((c) => c.quote);
  const used = (txt) => quotes.some((q) => q && (txt.includes(q) || q.includes(txt)));
  if (a.mode === "claude" && a.sent?.length) {
    return `<section class="shown"><div class="section-head"><h3>What Claude was shown</h3><span class="small muted">${a.sent.length} search results, each paragraph a citable block. Green = cited.</span></div>
      ${a.sent.map((x, i) => `<details ${i === 0 ? "open" : ""}><summary><span class="mono">#${i}</span>${esc(x.title)}</summary><div class="blocks">${x.blocks.map((b) => `<div class="${used(b) ? "used" : ""}">${esc(b)}</div>`).join("")}</div></details>`).join("")}</section>`;
  }
  return `<section class="shown"><div class="section-head"><h3>How the answer was put together</h3><span class="small muted">No language model in this mode. Every paragraph of the top three passages was scored against the question again, and the best three are quoted. Green = quoted.</span></div>
    ${s.hits.slice(0, 3).map((h, i) => `<details ${i === 0 ? "open" : ""}><summary><span class="mono">${esc(h.cite)}</span>${esc(h.title)}</summary><div class="blocks">${h.blocks.map((b) => `<div class="${used(b.text) ? "used" : ""}">${esc(b.text)}</div>`).join("")}</div></details>`).join("")}</section>`;
}

async function rerunHood(over) {
  const base = state.hood[state.collection] || run().search;
  const settings = { ...base.trace.settings, ...over };
  state.hood[state.collection] = await api("/api/search", { collection: state.collection, q: base.question, mode: settings.mode, jurisdiction_filter: settings.jurisdiction_filter });
  render();
}

async function fillPicker() {
  const sel = $("#pick-q");
  if (!sel) return;
  const qs = await loadQuestions();
  sel.insertAdjacentHTML("beforeend", qs.map((q) => `<option value="${esc(q.q)}">${esc(q.id)} · ${q.answerable ? "" : "(unanswerable) "}${esc(q.q)}</option>`).join(""));
  sel.addEventListener("change", async () => {
    if (!sel.value) return;
    location.hash = "#hood";
    await askQuiet(sel.value);
  });
}

async function askQuiet(q) {
  const key = state.collection;
  const r = (state.runs[key] = { q, phase: "searching" });
  delete state.hood[key];
  r.search = await api("/api/search", { collection: key, q });
  r.answer = await api("/api/answer", { collection: key, q }).catch((e) => ({ status: "refused", segments: [{ text: e.message, cites: [] }], citations: [], mode: state.meta.mode }));
  r.phase = "done";
  render();
}

/* ---------- corpus ---------- */
function renderCorpus(view) {
  const c = col();
  const cs = state.corpus;
  view.innerHTML = `
    <div class="section-head"><h2>Corpus</h2><span class="label">${esc(c.title)}</span></div>
    <p class="lede">Only official texts, downloaded from the publisher and parsed into provisions: sections, articles, regulations, recitals. Pick a document to see how its provisions were cut into the passages search works on.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Document</th><th>Place</th><th>Kind</th><th>Provisions</th><th>Words</th><th>Retrieved</th><th>Terms of reuse</th></tr></thead>
      <tbody>${c.docs.map((d) => `
        <tr class="pick ${cs.doc === d.id ? "on" : ""}" data-doc="${esc(d.id)}">
          <td><span class="doc-title">${esc(d.title)}</span><span class="sub"><a href="${esc(d.url)}" target="_blank" rel="noopener">official source ↗</a>${d.note ? ` · ${esc(d.note)}` : ""}</span></td>
          <td><span class="chip c-muted">${esc(PLACE[d.jurisdiction] || d.jurisdiction)}</span></td>
          <td>${esc(d.kind)}</td><td class="num">${d.provisions}</td><td class="num">${d.words.toLocaleString()}</td>
          <td class="num">${esc(d.retrieved || "–")}</td><td><span class="licence">${esc(d.licence)}</span></td>
        </tr>`).join("")}</tbody>
    </table></div>
    <div class="corpus-grid">
      <div class="stack" style="display:grid;gap:12px">
        ${cs.provs ? `<input class="search" id="prov-filter" placeholder="Filter provisions…" aria-label="Filter provisions">
          <div class="prov-list" id="prov-list">${provListHTML(cs.provs, "")}</div>` : `<div class="empty">Pick a document above.</div>`}
        <div class="card"><p class="label">How the text is cut</p><ul class="rules" style="margin-top:10px">
          <li>Only at the document's own paragraph breaks, never mid-paragraph, never across two provisions.</li>
          <li>About 220 words per passage, at most 320; a single paragraph longer than that is split at sentences.</li>
          <li>Each passage carries a header (document, citation, title) that is searched with it.</li>
          <li>A passage that starts mid-list carries the lead-in it hangs from, shown in grey, so "(b) …" still makes sense.</li>
        </ul></div>
      </div>
      <div class="prov-text" id="prov-text">${provDetailHTML(cs.detail)}</div>
    </div>`;
  $$("tr[data-doc]").forEach((tr) => tr.addEventListener("click", async () => {
    cs.doc = tr.dataset.doc;
    cs.detail = null;
    cs.provs = await api(`/api/collections/${state.collection}/docs/${cs.doc}`);
    render();
  }));
  const filter = $("#prov-filter");
  if (filter) filter.addEventListener("input", () => { $("#prov-list").innerHTML = provListHTML(cs.provs, filter.value); wireProvs(); });
  wireProvs();
}

function provListHTML(provs, f) {
  const q = f.trim().toLowerCase();
  return provs.filter((p) => !q || `${p.label} ${p.title}`.toLowerCase().includes(q)).map((p) => `
    <button type="button" data-prov="${esc(p.id)}" class="${state.corpus.prov === p.id ? "on" : ""}"><span><span class="mono">${esc(p.label)}</span> ${esc(p.title)}</span><span class="score">${p.chunks} · ${p.words}w</span></button>`).join("");
}

function wireProvs() {
  $$("[data-prov]").forEach((b) => b.addEventListener("click", async () => {
    state.corpus.prov = b.dataset.prov;
    state.corpus.detail = await api(`/api/collections/${state.collection}/provision?id=${encodeURIComponent(b.dataset.prov)}`);
    $$("[data-prov]").forEach((x) => x.classList.toggle("on", x.dataset.prov === state.corpus.prov));
    $("#prov-text").innerHTML = provDetailHTML(state.corpus.detail);
  }));
}

function provDetailHTML(d) {
  if (!d) return `<div class="empty">Pick a provision to see its passages.</div>`;
  const p = d.provision;
  return `<div class="section-head"><h3>${esc(p.label)}: ${esc(p.title)}</h3><a class="small" href="${esc(p.url)}" target="_blank" rel="noopener">official text ↗</a></div>
    ${p.path ? `<p class="small muted">${esc(p.path)}</p>` : ""}
    <p class="small muted">${d.chunks.length} passage${d.chunks.length === 1 ? "" : "s"}.</p>
    ${d.chunks.map((c) => `<div class="chunk-box"><div class="chunk-head"><span>${esc(c.id)}</span><span>${esc(c.cite)} · ${c.words} words</span></div>
      ${c.context ? `<div class="ctx">${esc(c.context)}</div>` : ""}
      ${c.blocks.map((b) => `<p>${esc(b.text)}</p>`).join("")}</div>`).join("")}`;
}

/* ---------- evals ---------- */
async function renderEvals(view) {
  if (!state.evals) state.evals = await api("/api/evals");
  const ev = state.evals;
  const job = state.job;
  const running = job && job.state === "running" && job.kind === "eval";
  const res = (ev.collections || []).find((c) => c.collection === state.collection);
  view.innerHTML = `
    <div class="section-head"><h2>Evals</h2><span class="label">${esc(col().title)}</span></div>
    <p class="lede">Hand-labelled questions, each with the provisions that answer it, plus questions these documents cannot answer. Every search setup runs on every question, so you can see what each stage adds.</p>
    <div class="card controls-row">
      <button class="btn btn-accent" id="run-evals" ${running ? "disabled" : ""}>Run the evals</button>
      <span class="small muted">${ev.embed_model ? `Embeddings ${esc(ev.embed_model)} · re-ranker ${esc(ev.rerank_model)}` : ""}${res ? ` · last run ${esc(res.ran_at)}` : ""}</span>
    </div>
    <div id="eval-progress">${progressHTML(job)}</div>
    ${res ? evalTableHTML(res) : `<div class="empty">No results yet. Run the evals.</div>`}
    <div class="card"><p class="label">How this is measured</p><ul class="rules" style="margin-top:10px">
      <li><b>Recall@k</b>: the share of answerable questions with a correct provision in the top k passages. For long sections, the passage must hold the right paragraph.</li>
      <li><b>MRR</b>: the average of 1 / (rank of the first correct passage). 1.0 means always first.</li>
      <li><b>Refusal</b>: the re-ranker's best score separates answerable from unanswerable questions. The line is chosen on the dev half and reported on the test half, so it isn't tuned on what it's scored on.</li>
      <li>The questions are written in plain language, not the law's wording, so keyword search gets no free help.</li>
    </ul></div>`;
  $("#run-evals").addEventListener("click", async () => {
    try { state.job = await api("/api/jobs/eval", { collections: [state.collection] }); render(); } catch (e) { toast(e.message); }
  });
  $$("[data-inspect]").forEach((b) => b.addEventListener("click", async () => { location.hash = "#hood"; await askQuiet(b.dataset.inspect); }));
}

function progressHTML(job) {
  if (!job || !job.kind) return "";
  if (job.state === "running") {
    const share = job.total ? job.done / job.total : 0;
    return `<div class="card progress"><div class="status running"><span class="pulse"></span><span>Running evals<span class="phase">${esc(job.phase || "starting")} · ${job.done} of ${job.total || "…"} searches</span></span></div>
      <div class="bar"><span style="width:${(share * 100).toFixed(2)}%"></span></div></div>`;
  }
  if (job.state === "failed") return `<div class="status failed">The eval run failed: ${esc(job.message)}</div>`;
  if (job.state === "done") return `<div class="status done">Evals finished: ${job.done} searches in ${fmtMs((job.finished - job.started) * 1000)}.</div>`;
  return "";
}

function evalTableHTML(r) {
  const best = Math.max(...r.variants.map((v) => v["recall@1"]));
  const bar = (x, strong) => `<div class="mbar ${strong ? "" : "weak"}"><i style="width:${Math.max(2, x * 100)}px"></i>${pct(x)}</div>`;
  const last = r.variants[r.variants.length - 1];
  const misses = Object.entries(r.per_question).filter(([, pq]) => pq.rerank_filter && pq.rerank_filter.rank !== 1);
  return `
    <div class="table-wrap"><table class="ablation">
      <thead><tr><th>Search setup</th><th>Recall@1</th><th>Recall@5</th><th>MRR</th><th>Missed</th><th>p50</th></tr></thead>
      <tbody>${r.variants.map((v) => `<tr><td>${esc(v.label)}</td><td class="bar-cell">${bar(v["recall@1"], v["recall@1"] === best)}</td><td class="bar-cell">${bar(v["recall@5"], v === last)}</td><td class="num">${v["mrr@10"].toFixed(2)}</td><td class="num">${v.missed}</td><td class="num">${fmtMs(v.p50_ms)}</td></tr>`).join("")}</tbody>
    </table></div>
    ${last.refusal ? `<div class="tiles">
      <div class="tile"><b>${r.answerable} / ${r.unanswerable}</b><span>answerable / unanswerable questions</span></div>
      <div class="tile"><b>${last.refusal_auc.toFixed(2)}</b><span>how well the best score separates them (AUC; 0.5 is chance, 1.0 perfect)</span></div>
      <div class="tile ok"><b>${pct(last.refusal.test_kept_answerable)}</b><span>answerable test questions let through the floor</span></div>
      <div class="tile held"><b>${pct(last.refusal.test_refused_unanswerable)}</b><span>unanswerable test questions refused at the floor</span></div>
      <div class="tile held"><b>${pct(last.refusal.test_flagged_unanswerable)}</b><span>unanswerable ones flagged as weak or refused</span></div>
    </div>
    <p class="small muted">A question about a law that isn't here (California's CCPA, the UK GDPR) still finds closely related text, so search alone can't refuse it reliably. That job is the reader's: with Claude on, the model refuses when the passages don't cover the question, measured by <span class="mono">eval --answers</span>.</p>` : ""}
    ${misses.length ? `<div class="card"><p class="label">Where the full pipeline's first passage was not a correct one</p><ul class="misses" style="margin-top:8px">${misses.map(([id, pq]) => `
      <li><span><span class="mono">${esc(id)}</span> ${esc(pq.q)} <span class="sub small muted">correct at ${pq.rerank_filter.rank ? `#${pq.rerank_filter.rank}` : "none of the top 10"}; first was ${esc(pq.rerank_filter.top[0] || "–")}</span></span>
      <button class="btn btn-ghost btn-sm" data-inspect="${esc(pq.q)}">Inspect</button></li>`).join("")}</ul></div>` : ""}`;
}

/* ---------- live job stream ---------- */
function connectStream() {
  const es = new EventSource("/api/job/stream");
  es.onmessage = (e) => {
    const job = JSON.parse(e.data);
    const was = state.job && state.job.state;
    state.job = job;
    if (state.tab === "evals" && $("#eval-progress")) $("#eval-progress").innerHTML = progressHTML(job);
    if (was === "running" && job.state !== "running") { state.evals = null; if (state.tab === "evals") render(); }
    if ($("#run-evals")) $("#run-evals").disabled = job.state === "running";
  };
  es.onerror = () => { es.close(); setTimeout(connectStream, 2000); };
}

/* ---------- start ---------- */
(async function start() {
  render();
  try { await loadMeta(); } catch (e) { $("#view").innerHTML = `<div class="status failed">Could not reach the server: ${esc(e.message)}</div>`; return; }
  window.addEventListener("hashchange", setTab);
  setTab();
  connectStream();
  const poll = async () => { // the models load in the background on first start; the passage count appears when they are ready
    if (state.meta.ready) return;
    await new Promise((r) => setTimeout(r, 1500));
    const m = await api("/api/meta").catch(() => null);
    if (m) { state.meta = { ...state.meta, ...m }; if (m.ready && state.tab === "ask" && !run().search) render(); }
    poll();
  };
  poll();
})();
