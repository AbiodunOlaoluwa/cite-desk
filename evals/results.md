# Retrieval evals

Embeddings: `BAAI/bge-small-en-v1.5`. Re-ranker: `Xenova/ms-marco-MiniLM-L-6-v2`. Run: 2026-10-19 22:16 UTC.

Recall@k: share of answerable questions with a correct provision in the top k. MRR: average of 1/rank of the first correct one. A question about a long section counts only if the chunk holds the right paragraph.

## outreach (56 answerable, 12 unanswerable)

| Variant | Recall@1 | Recall@3 | Recall@5 | MRR@10 | Missed (top 10) | p50 ms | p95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Keyword only (BM25) | 25% | 48% | 61% | 0.39 | 19 | 0.7 | 2.0 |
| Meaning only (embeddings) | 50% | 73% | 79% | 0.62 | 6 | 16.2 | 24.8 |
| Hybrid (BM25 + embeddings, fused) | 48% | 64% | 79% | 0.59 | 9 | 21.8 | 37.1 |
| Hybrid + re-rank | 55% | 79% | 86% | 0.67 | 4 | 276.8 | 1369.3 |
| Hybrid + re-rank + jurisdiction filter | 64% | 82% | 89% | 0.73 | 4 | 282.2 | 372.1 |

**Refusing when the documents don't cover it.** The signal is the best re-rank score (AUC 0.76: it separates answerable from unanswerable questions only partly, because a question about a law that is not here still finds closely related text). Both lines are chosen on dev and measured on test:

- Refuse below -10.48: keeps 96% of answerable questions, refuses 33% of unanswerable ones before any model is called.
- Flag as a weak match below -2.056: 75% of answerable questions clear it, 67% of unanswerable ones are flagged.
- With Claude switched on, the model reads the passages and refuses when they don't cover the question; `python -m cite_desk eval --answers` measures that (needs an API key).

**Where the full pipeline's first result was not a correct provision:**

- `o03` How long does the unsubscribe link in my marketing email have to keep working after I send it? Correct at rank 3; first result CASL s. 11(1)–(3).
- `o07` If an agency sends cold emails for my company, who is responsible if they break the rules? Correct at rank 3; first result FTC guide.
- `o08` What counts as a transactional or relationship email that is exempt from most CAN-SPAM rules? Correct at rank 3; first result 16 CFR § 316.3(c).
- `o11` Is it allowed to collect email addresses from websites with a scraper and then send them marketing? Correct at rank 8; first result PECR reg. 23(a)–(d).
- `o13` Do individual US states still have their own anti-spam email laws on top of CAN-SPAM? Correct at rank 4; first result FTC guide.
- `o15` Are marketing messages sent straight to mobile phones handled differently under CAN-SPAM? Correct at rank 4; first result FTC guide.
- `o18` How long do I have to keep honouring someone's request not to be called by my sales team? Correct at rank 3; first result FTC guide.
- `o19` What can a consumer recover in court for an illegal robocall? Correct at rank 3; first result 47 CFR § 64.1200(a)–(b).
- `o24` Is there a limit on how many calls a predictive dialer can drop? Correct at rank 3; first result 47 CFR § 64.1200(a).
- `o27` Can someone in the EU tell me to stop using their data for marketing, and do I have to listen? Correct at rank 2; first result GDPR Recital 70.
- `o29` What makes consent valid under the GDPR? Correct at rank 2; first result GDPR Recital 43.
- `o30` I bought a list of EU leads from a data provider. What do I have to tell those people? Correct at rank none in top 10; first result GDPR Recital 31.
- `o33` Does the GDPR apply to a US startup selling to customers in Germany? Correct at rank 5; first result GDPR Recital 25.
- `o36` How long am I allowed to keep prospect data under the GDPR? Correct at rank none in top 10; first result GDPR Recital 171.
- `o39` What is the soft opt-in in the UK? Correct at rank 7; first result PECR reg. 16A(8).
- `o43` Do I need consent for analytics cookies on a UK website? Correct at rank none in top 10; first result PECR reg. 14(1)–(5).
- `o49` Can I email a Canadian business contact whose address is listed on their company website? Correct at rank 2; first result CASL s. 11(1)–(3).
- `o50` Does Canada's anti-spam law apply to emails I send from Nigeria to people in Toronto? Correct at rank 5; first result CASL s. 6(1)–(5).
- `o53` How quickly must a data breach be reported to the NDPC? Correct at rank 2; first result GAID 2025, Schedule 2.
- `o56` Does a company in Nigeria need a data protection officer? Correct at rank none in top 10; first result GAID 2025, Schedule 7.

## lagos-tenancy (18 answerable, 4 unanswerable)

| Variant | Recall@1 | Recall@3 | Recall@5 | MRR@10 | Missed (top 10) | p50 ms | p95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Keyword only (BM25) | 78% | 94% | 94% | 0.86 | 1 | 0.1 | 0.1 |
| Meaning only (embeddings) | 78% | 83% | 94% | 0.83 | 1 | 3.9 | 4.2 |
| Hybrid (BM25 + embeddings, fused) | 78% | 94% | 94% | 0.86 | 1 | 4.0 | 4.4 |
| Hybrid + re-rank | 83% | 94% | 94% | 0.90 | 0 | 176.0 | 194.8 |
| Hybrid + re-rank + jurisdiction filter | 83% | 94% | 94% | 0.90 | 0 | 176.3 | 198.3 |

**Refusing when the documents don't cover it.** The signal is the best re-rank score (AUC 0.736: it separates answerable from unanswerable questions only partly, because a question about a law that is not here still finds closely related text). Both lines are chosen on dev and measured on test:

- Refuse below -5.034: keeps 89% of answerable questions, refuses 0% of unanswerable ones before any model is called.
- Flag as a weak match below -3.851: 89% of answerable questions clear it, 0% of unanswerable ones are flagged.
- With Claude switched on, the model reads the passages and refuses when they don't cover the question; `python -m cite_desk eval --answers` measures that (needs an API key).

**Where the full pipeline's first result was not a correct provision:**

- `l13` What happens after the notice to quit expires and the tenant refuses to leave? Correct at rank 2; first result Tenancy Law 2011, s. 24.
- `l14` If a monthly tenant owes six months of rent, does the tenancy end? Correct at rank 2; first result Tenancy Law 2011, s. 4(1)–(5).
- `l16` Is the landlord allowed to enter my flat whenever they want? Correct at rank 6; first result Tenancy Law 2011, s. 12(a)–(b).

