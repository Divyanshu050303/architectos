# ADR-023: Project knowledge as cited, lexical evidence in PostgreSQL

- Status: accepted
- Date: 2026-10-02

## Context

The LLM Architecture Agent and AI Architecture Diff need context from a project's documents and
records. That context must be:

- grounded in a source a person can open, at an exact location;
- scoped to what the caller may see;
- never presented as fact.

What the repository audit found:

- The `ai/rag` and `ai/grounding` scaffolds, the knowledge worker and the `knowledge/patterns` files
  were empty.
- There was no embedding provider. Anthropic offers no embeddings API.
- The database image has no `pgvector`.

Retrieval-augmented generation is where it is easiest to:

- cite a passage the source doesn't contain;
- treat a high similarity as relevance or truth;
- leak another tenant's passage through a shared index;
- follow instructions planted in a document;
- pad results to a requested count with unrelated text.

## Decision

### What is indexed

- **Project-scoped sources, read safely.**
  - Uploaded Markdown and plain text, inline and bounded.
  - ADRs and requirement versions, read as explicit snapshots.
  - Nothing is executed, rendered, fetched or followed. Secret-looking values are redacted before
    anything is stored.
- **Versioned, append-only index.** Each successful ingestion of new content is version `n + 1`,
  stored in full before the source points at it. The rules:
  - unchanged content creates nothing;
  - a failure keeps the last known-good version;
  - passages keep stable ids while their words don't change;
  - a changed record makes its snapshot `stale` until someone re-indexes it; nothing is re-indexed
    automatically.

### How it is retrieved

- **Lexical and identifier retrieval only, in PostgreSQL.**
  - Passages store their terms (`knowledge-terms@1`) and the identifiers they name, both GIN-indexed.
  - SQL pre-filters the version in force of the project's active sources.
  - A pure engine decides, re-checking scope: exact identifiers first, then passages holding at least
    half the query's terms.
  - No embeddings or vectors are stored, and there is no vector database, cache or search service.
- **Evidence, not answers.**
  - Every passage carries an exact citation (source, version, document, passage, and only the
    locator parts the source supports), how it was found, `user_provided`, and the record's status.
  - There is no score.
  - An empty result is `insufficient_evidence`, never padded and never evidence against.
  - Retrieved text is untrusted data.
- **One boundary for consumers:** `KnowledgeRetriever` (the service), authorized per project. It
  returns evidence, never tables, vectors or persistence models.

### What protects it

- **Access and storage.**
  - `knowledge.read` for viewers and `knowledge.manage` for members.
  - Same-project foreign keys, and project access resolved on every request: no cache, so revocation
    is immediate.
  - Migration `0023`, with triggers that keep runs, versions, documents and passages append-only, and
    a source's identity fixed.
- **Measured, not claimed.** A versioned labelled set, its thresholds as a regression gate, and an
  integration test showing the API returns exactly what the evaluation measures.

## Consequences

- Every result can be checked against its source, and no tenant's passage reaches another. Results
  are reproducible.
- **Recall depends on vocabulary.** A question must share the document's words, or name an
  identifier. "fail over" doesn't find "failover", and the v1 evaluation records that miss.
- **Adding semantic retrieval** is a new decision. It needs all of:
  - a typed embedding provider port (model id and version, dimensionality, limits, retryable errors,
    timeouts);
  - vectors stored with that identity, never compared across models;
  - `pgvector` or an equivalent in an approved image;
  - an evaluation on the labelled set showing it improves recall without false evidence before it
    ranks anything.

  The lexical stage and citations stay.
- **Ingestion is synchronous** and bounded: 512 KiB documents, 5,000 passages. Larger corpora, more
  formats or connectors need their own tested adapters, and likely a job framework.
- **Freshness is checked on read and retrieval,** not pushed by the decision and requirement
  services. That keeps those domains uncoupled, so listings may show a status from the last check.
- **Frontend.** The web app has no knowledge UI. Its evidence mock is a different concept; see
  [docs/frontend/knowledge-contract.md](../frontend/knowledge-contract.md).
