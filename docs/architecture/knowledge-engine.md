# Knowledge Engine

Project knowledge makes a project's documents and records retrievable as **evidence with citations**,
so people, deterministic engines and future AI agents can use it safely.

**Sources:**

- uploaded Markdown or plain-text documents;
- ArchitectOS decisions (ADRs) and requirements, read as snapshots.

**Retrieval** returns passages, the sources' own words, each located exactly. It never returns
answers.

> Retrieving a passage verifies nothing. A passage is what a source says, not an established fact.
> No passage is scored. Nothing found is *insufficient evidence*, never evidence that a statement is
> false or that a property is absent. Retrieved text is untrusted data: never instructions.

The decisions are recorded in [ADR-023](../adr/ADR-023-lexical-evidence-retrieval.md), and the API
contract is in [docs/api/knowledge.md](../api/knowledge.md).

## Architecture and module boundaries

| Layer | Module | Responsibility |
|---|---|---|
| Domain | `core/domain/knowledge` | Contracts (`values`, `sources`, `documents`, `ingestion`, `retrieval`, `uploads`), the `KnowledgeEngine` and `KnowledgeRetriever` ports, the repository protocol, and `KnowledgeService`. Stores nothing itself; uses no network. |
| Engine | `engines/knowledge` | Adapters (`adapters`), redaction (`redaction`), structure (`structure`), chunking (`chunking`), terms (`terms`), retrieval (`retrieval`), and the `DeterministicKnowledgeEngine`. Pure: the same inputs give the same output. |
| Persistence | `persistence/models/knowledge.py`, `persistence/repositories/knowledge.py` | Five tables, created by migration `0023`, and the retrieval prefilter in SQL. |
| API | `apps/api/routes/knowledge.py`, `apps/api/schemas/knowledge.py` | Nine endpoints under `/api/v1/projects/{projectId}`. |
| Evaluation | `ai/evaluation/knowledge.py`, `ai/evaluation/datasets/knowledge/v1` | The labelled retrieval set and its regression gate. |

The empty `ai/rag` scaffolds are not used. Consumers call `KnowledgeRetriever.retrieve(project_id,
user_id, query)`; the service implements it. They never read the knowledge tables directly.
PostgreSQL is the only store: there is no vector database, cache or search service.

## Supported source types and limitations

| Type | Input | Located by | Not supported |
|---|---|---|---|
| `markdown` | inline text, `.md` / `.markdown`, ≤ 512 KiB | heading path and lines | setext headings and HTML are kept as text, never interpreted |
| `text` | inline text, `.txt` / `.text`, ≤ 512 KiB | lines | — |
| `decision` | an ADR of the project, by id | record and field (`ADR-3 context`) | lines (a record has none) |
| `requirement` | a requirement of the project, by id, at its current version | record and field (`REQ-12 statement`) | lines |

Other inputs are refused, never guessed:

- PDF, Office, HTML pages and archives;
- URLs and remote fetching;
- the component catalog and architecture revisions.

A type comes from the request or the file extension. A name and a type that disagree are refused.

## Ingestion lifecycle

Registering or re-indexing reads the source synchronously, in one transaction, under the source's row
lock:

1. **Validate** the request: path, size, type, and whether the source belongs to the project and to
   this input.
2. **Extract**:
   - documents: the BOM is removed and line endings normalized; control characters, bidirectional
     overrides, overlong lines and empty documents are refused;
   - records: their fields are read.
3. **Redact** secret-looking values (`knowledge-redaction@1`), then compute the content checksum.
4. **Compare** the checksum with the version in force. If they are equal, the run ends `unchanged`
   and nothing is created.
5. **Structure and chunk** the content into passages.
6. **Store, in order:**
   1. the run;
   2. the new version `n + 1`, with its document and every passage (its terms and identifiers);
   3. the source pointing at that version.

A run ends `completed`, `completed_with_warnings`, `unchanged` or `failed`. A failure stores the
failed run and changes nothing else: the last known-good version stays in force, and stays stale if it
was.

Registering the same path or record twice is `409 knowledge_source_exists`, with the existing source.
A partial unique index enforces this under concurrency.

## Normalization and chunking

**Structure** (`knowledge-structure@1`):

- Markdown is divided into sections by ATX headings outside fenced code.
- Inside a section, segments are separated by blank lines. A fenced code block is a single segment.
- Each segment keeps its kind (code, table, list, paragraph) and its exact lines.
- Plain text is paragraphs; records are fields.
- No word is changed. Numbers, units, identifiers and code are kept as written.

**Chunking** produces passages:

- Strategies: `sections@1` for Markdown, `paragraphs@1` for text, `fields@1` for records.
- Whole segments of one section are packed while the passage, the source's own lines, stays within
  `max_chars`: 200 to 4,000, default 1,500, recorded with every version.
- A larger segment is split between lines (code, tables and lists only there), then between words.
- There is no overlap.
- Sections with only a heading are skipped and counted in a warning.

**Identifiers** (`knowledge-identifiers@1`): `ADR-<n>`, `REQ-<n>`, and identifiers in backticks.

**Stable ids:**

- A passage's id comes from its source, the strategy, its heading path, its words and its occurrence.
  An unchanged passage keeps its id across versions.
- A document's id comes from its source and checksum.

## Embedding provider configuration

There is **no embedding provider**. No embeddings or vectors are stored, and semantic similarity is
not configured. Every search result says so in its `limitations`.

Adding one is a separate, recorded decision ([ADR-023](../adr/ADR-023-lexical-evidence-retrieval.md))
that would need all of these:

- a typed provider port (model id and version, dimensionality, limits, retryable errors, timeouts);
- vectors stored with that identity;
- a refusal to compare vectors from different models;
- an evaluation on the labelled set before it ranks anything.

PostgreSQL would need `pgvector`. The current image (`postgres:16-alpine`) does not have it.

## Indexing lifecycle and retries

Index status is separate from lifecycle (`active`, `archived`):

| Status | Meaning |
|---|---|
| `pending` | Registered, not indexed yet. |
| `processing` | An ingestion is running. Ingestions are synchronous, so this is never stored. |
| `indexed` | A version is in force. |
| `failed` | Never indexed. |
| `stale` | A decision or requirement source whose record now reads differently from the version in force. |

Staleness is checked when the source is read and when its passages are retrieval candidates, under
the source's lock. A stale source is still searched, each passage marked stale, until someone
re-indexes it. Nothing is re-indexed automatically.

**Retries:** `POST …/ingestions {retryOf}` names the failed run it repeats. Only a failed run can be
retried. **Archiving** stops searching and re-indexing, and keeps everything.

## Retrieval

`POST …/knowledge/search` runs these stages (`knowledge-retrieval@1`):

1. **Validate** the request. Terms or identifiers are required, at most 500 characters and 20
   identifiers, and a limit of 1 to 20.
2. **Authorize** with `knowledge.read`, and resolve the project from the caller, never from the
   request.
3. **Prefilter** in SQL: passages of the version in force of the project's active sources, within the
   filters, that name a requested identifier or share a term (GIN indexes on `terms` and
   `identifiers`). At most 500 candidates, identifiers and most shared terms first.
4. **Check freshness** of the decision and requirement sources among the candidates.
5. **Decide** in the engine. It re-checks every candidate's project, source, type and staleness
   against the scope; it never trusts the prefilter.
   - **Identifier matches first.** More requested identifiers named rank higher.
   - **Then lexical matches:** a passage must contain at least half of the query's terms
     (`knowledge-terms@1`).
6. **Order** by matched terms, then occurrences, then shorter passages, then source and position.
   Duplicates are removed, and at most `limit` passages are returned, never padded.
7. **Cite** each passage and state the result's limitations.

The terms rule (`knowledge-terms@1`) lower-cases words, drops common English function words, and
reduces plurals of words of four letters or more. It does no other stemming and knows no synonyms.

## Citation and evidence semantics

A citation is `{sourceId, sourceName, sourceType, sourceVersion, documentId, chunkId, locator,
reference}`:

- The `locator` has only the parts the source supports: `headingPath` and `lineStart`/`lineEnd` for
  documents, `record` and `field` for records. Nothing is invented: no page and no section a source
  doesn't have.
- `reference` reads, for example, `Runbook (v2): Orders runbook > Failover (lines 5-7)`.

Each passage also carries:

| Field | Meaning |
|---|---|
| `verification` | Always `user_provided`: discovery's vocabulary, meaning a person wrote it. |
| `recordStatus` | The record's status when read (`proposed`, `accepted`, `active`…), stated and never interpreted. |
| `stale` | Whether the source is stale. |
| `method` | `identifier` or `lexical`. |
| `matched` | What the passage matched. |
| `rank` | An order for this query, not a measure of truth. |

`insufficientEvidence` is true when nothing in scope supports the query. It is not evidence against
the query. Consumers handing passages to a language model keep them apart from the instructions.

## Authorization and tenant isolation

| Permission | Who | Can |
|---|---|---|
| `knowledge.read` | viewers and up | search, and read sources, runs and passages |
| `knowledge.manage` | members and up | register, re-index and archive |

- **Project access** is resolved for every operation, inside the transaction, so a membership revoked
  takes effect on the next request. Nothing is cached.
- **Scoping:** every query is filtered by project. Every foreign key is same-project. A source, run or
  passage of another project or organization is not found, the same as one that doesn't exist.
- **Filters** only narrow: a filter naming another project's source finds nothing.

## Resource limits

| Limit | Value |
|---|---|
| Document content | 512 KiB, ≤ 20,000 lines of ≤ 10,000 characters |
| Request body for registering or re-indexing | 4 MiB (`MAX_KNOWLEDGE_BODY_BYTES`, default `4194304`) |
| Other request bodies | 64 KiB (`MAX_REQUEST_BODY_BYTES`) |
| Passages per document | 5,000 |
| Passage length | 4,000 characters (`max_chars` 200–4,000, default 1,500) |
| Query | 500 characters, 20 identifiers, 50 source filters |
| Results | ≤ 20 per search (default 10) |
| Retrieval candidates | 500 per search |
| Rate limits (per user per hour) | 120 registrations and re-indexes (`knowledge_ingest`), 600 searches (`knowledge_search`) |

## Error handling

| Status | Error |
|---|---|
| 422 | `invalid_knowledge_request` (`details: field, reason`), `validation_error` |
| 404 | `knowledge_source_not_found`, `knowledge_chunk_not_found`, `ingestion_run_not_found` (and `decision_not_found`, `requirement_not_found` when registering) |
| 409 | `knowledge_source_exists` (`details.sourceId`), `invalid_knowledge_transition` |
| 413 | `payload_too_large` |
| 429 | `rate_limited` |

- **Content problems** are not request errors. They are failed runs with codes such as
  `invalid_characters`, `bidirectional_override`, `line_too_long`, `nothing_to_index`,
  `too_many_chunks` and `record_deleted`. Each carries the stage and the line or field, never the
  content.
- **Unexpected failures** return `500 internal_error` without details and store nothing.
- **Corruption:** a stored passage whose words no longer make its id is refused as corrupted
  (`invalid_knowledge_record`).

## Database migrations

Migration `0023` adds five tables:

| Table | Contents | Write rule (enforced by triggers) |
|---|---|---|
| `knowledge_sources` | identity, index status, version in force, lifecycle | identity never changes; never deleted or un-archived; the version in force never goes back; one active source per path and per record |
| `knowledge_ingestion_runs` | each run | append-only |
| `knowledge_source_versions` | each indexed reading | append-only |
| `knowledge_documents` | one per version | append-only |
| `knowledge_chunks` | passages with `terms` and `identifiers` (GIN) | append-only |

Downgrading to `0022` removes the tables and everything in them.

## Observability

Metrics go through the `Metrics` port, with labels that are identifiers only:

- `knowledge.ingestions` and `knowledge.ingestion_seconds` (by `status`, `source_type`);
- `knowledge.retrievals` and `knowledge.retrieval_seconds` (by `outcome`: `found` or
  `insufficient_evidence`).

Audit events are `knowledge_source.registered`, `knowledge_source.ingested` and
`knowledge_source.archived`. They carry identifiers, types, statuses, counts and error codes only.
Queries, content, names and paths are never logged, measured or audited.

## Local development and tests

```bash
make db-up && uv run alembic upgrade head
uv run pytest tests/unit/knowledge                           # domain, adapters, chunking, ingestion, retrieval, service
uv run pytest tests/integration/api/test_knowledge.py tests/integration/api/test_knowledge_evaluation.py
uv run pytest tests/security/test_knowledge_safety.py tests/security/test_traceability_knowledge_engine.py
uv run python -m ai.evaluation.knowledge                     # the retrieval evaluation report
make lint typecheck test-unit test-integration test-security test-eval migrate-check
```

## Operational troubleshooting

| Symptom | Cause | What to do |
|---|---|---|
| Run `failed` at `extracting` with `invalid_characters` or `bidirectional_override` | The document has control or bidirectional characters, at the line given. | Clean the text and re-index. |
| Run `failed` with `nothing_to_index` | The document is empty, or has headings only. | Add content. |
| Run `unchanged` | The content's checksum equals the version in force. Line endings and BOM don't count. | Nothing to do: nothing was duplicated. |
| Source `stale` | Its decision or requirement changed, or the requirement was deleted. | Re-index: `POST …/ingestions {}`. A deleted requirement fails with `record_deleted`, and the last version stays. |
| `409 knowledge_source_exists` | The path or record is already a source. | Re-index the source named in `details.sourceId`. |
| `insufficientEvidence` for a known passage | Fewer than half the query's words appear in it as written ("fail over" ≠ "failover"; no synonyms). | Use the document's words, or an identifier. |
| `413 payload_too_large` | The body is over `MAX_KNOWLEDGE_BODY_BYTES`. | Keep documents ≤ 512 KiB. |
| `429 rate_limited` | Too many ingestions or searches. | Wait for `Retry-After`. |

## Known limitations and future work

- **Retrieval:**
  - Lexical and identifier retrieval only: no semantic similarity, stemming beyond plurals, or
    synonyms.
  - Recall depends on the question using the source's words. The v1 evaluation has one known miss
    for this reason.
- **Evaluation:** the set is small (six documents, 24 queries). It shows the rules behave as
  documented, not retrieval quality on a real project.
- **Ingestion:** synchronous and bounded; no background worker, scheduling or connectors.
- **Supported content:** no PDF, Office, HTML or archive sources, and no component catalog or
  architecture revision sources.
- **Freshness:** checked on read and retrieval only, not pushed when a record changes. `status` in
  listings reflects the last check.
- **Access:** no per-source access classification; access is project-scoped.
- **Future work:**
  - an embedding provider and evaluated hybrid retrieval (ADR-023 lists the conditions);
  - more source types, each with a tested adapter;
  - consumers: the LLM Architecture Agent and AI Architecture Diff milestones.
