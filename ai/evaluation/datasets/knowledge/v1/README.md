# Knowledge retrieval evaluation, v1

A small, labelled set that checks knowledge retrieval behaves as documented on representative
architecture text. It is **not** a measure of retrieval quality on a real project's documentation.

- `corpus/`: six documents, 2.8 KB in all:
  - five Markdown files: an orders runbook, a payments architecture, a capacity plan,
    observability, and order events;
  - one plain-text retention policy.
- `queries.jsonl`: 24 queries.
  - 20 are answerable, 3 of them by exact identifier (`ADR-7`, `REQ-12`, `payments-api`).
  - 3 are filtered to one source: 2 answerable and 1 unanswerable.
  - 4 have no answer in the corpus and must return insufficient evidence.
- `thresholds.json`: the floors the engine reached (and the ceilings for false evidence and
  duplicates). They only move up: when retrieval improves, raise them in the same change.

Run `python -m ai.evaluation.knowledge` for the report, or `make test-eval` for the regression gate.
An integration test (`tests/integration/api/test_knowledge_evaluation.py`) runs the same queries
through the API and PostgreSQL, and checks it returns the same passages.

## Known misses (v1)

`failover_how` ("how do we fail over the orders database") does not find the runbook's Failover
section. The terms rule matches whole words only:

- "fail over" is two words, while the heading says "failover";
- "do" and "we" are terms;
- so the passage holds 2 of the query's 6 terms, and fewer than half are matched.

This is the documented behavior of `knowledge-terms@1`: no stemming beyond plurals, and no
synonyms. It was not tuned away to fit this set.

## Limits

- Six documents and 24 questions cannot show precision or recall on a real corpus, on long
  documents, or on vocabulary that differs between question and source.
- Semantic similarity is not configured, so nothing here measures it.
- Records (ADRs, requirements) are evaluated in the unit and integration tests, not here.
