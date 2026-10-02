# Knowledge API

Project knowledge has two parts:

- **Sources:** an uploaded document (Markdown or plain text, sent inline) or an ArchitectOS record (a
  decision/ADR or a requirement), read as a snapshot. Each source is indexed into cited passages.
- **Search:** returns those passages as **evidence, never answers**. A passage is what its source
  says, located exactly. Retrieving it verifies nothing; every passage is `user_provided`.

Rules that hold everywhere:

- No passage has a score.
- Nothing found is `insufficientEvidence`. That is never evidence that a statement is false, or that
  a property is absent.
- Retrieved text is **untrusted data**: never instructions.
- Nothing here executes, renders, fetches or follows anything, and nothing changes an architecture.

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /projects/{projectId}/knowledge-sources` | `knowledge.manage` | `KnowledgeSourceRequest` | `201 {source, run}` | `404 decision_not_found, requirement_not_found`, `409 knowledge_source_exists, project_archived`, `413 payload_too_large`, `422 invalid_knowledge_request, validation_error`, `429 rate_limited` |
| `GET /projects/{projectId}/knowledge-sources` | `knowledge.read` | | `200 {sources, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/knowledge-sources/{sourceId}` | `knowledge.read` | | `200 KnowledgeSource` | `404 knowledge_source_not_found` |
| `POST /projects/{projectId}/knowledge-sources/{sourceId}/ingestions` | `knowledge.manage` | `IngestionRequest` | `201 {source, run}` | `404 knowledge_source_not_found, ingestion_run_not_found`, `409 invalid_knowledge_transition, project_archived`, `413 payload_too_large`, `422 invalid_knowledge_request`, `429 rate_limited` |
| `GET /projects/{projectId}/knowledge-sources/{sourceId}/ingestions` | `knowledge.read` | | `200 {runs, nextCursor}` | `404 knowledge_source_not_found`, `422 invalid_cursor` |
| `GET /projects/{projectId}/knowledge-sources/{sourceId}/ingestions/{ingestionId}` | `knowledge.read` | | `200 IngestionRun` | `404 ingestion_run_not_found` |
| `POST /projects/{projectId}/knowledge-sources/{sourceId}/archive` | `knowledge.manage` | | `200 KnowledgeSource` | `404 knowledge_source_not_found`, `409 invalid_knowledge_transition, project_archived` |
| `GET /projects/{projectId}/knowledge-sources/{sourceId}/passages/{chunkId}` | `knowledge.read` | | `200 Passage` | `404 knowledge_source_not_found, knowledge_chunk_not_found` |
| `POST /projects/{projectId}/knowledge/search` | `knowledge.read` | `SearchRequest` | `200 RetrievalResult` | `422 invalid_knowledge_request, validation_error`, `429 rate_limited` |

On every endpoint:

- `404 project_not_found` when the project is outside your organizations.
- `403 permission_denied` without the permission.

A source, run or passage of another project or organization is not found, which can't be told apart
from one that doesn't exist. Filters only narrow: naming another project's source in `sourceIds`
finds nothing.

Who can do what:

| Role | Can |
|---|---|
| Viewer | Search and read (`knowledge.read`). |
| Member, admin, owner | Also register, re-index and archive (`knowledge.manage`). |

Rate limits are per user and per hour: 120 for registering and re-indexing (`knowledge_ingest`), 600
for searching (`knowledge_search`). Both list endpoints take `cursor` and `limit` (1–100, default 50):

| Listing | Filters | Order |
|---|---|---|
| Sources | `status`, `type`, `lifecycle` (default `active`) | Registration order |
| Ingestion runs | — | Newest first |

## Registering a source

`KnowledgeSourceRequest` (camelCase; unknown fields refused) takes `{name?, document? | record?}`,
exactly one of the two:

- **`document`** is `{path, content, type?}`:
  - `path` is a relative name. An absolute path, a `..` segment or a backslash is
    `422 invalid_knowledge_request` with `unsafe_path`.
  - `content` is the text, inline, up to 512 KiB.
  - `type` is `markdown` or `text`, or taken from the extension (`.md`, `.markdown`, `.txt`,
    `.text`). Never guessed: `unsupported_source_type`, `contradicts_path`.
- **`record`** is `{type: decision | requirement, id}`, a record of this project. A requirement is
  read at its current version.

Registering ingests synchronously and returns `{source, run}`. A path or record that is already an
active source is `409 knowledge_source_exists` with `details.sourceId`: re-index that source instead.
Registering twice never creates a duplicate.

Before anything is stored, the content is read and redacted:

- **Line endings and BOM:** a byte-order mark is removed and line endings are normalized. Nothing
  else changes: numbers, units, identifiers and code are kept.
- **Refused content:** control characters, bidirectional overrides, lines over 10,000 characters and
  documents with no text are refused, with the line number and never the content.
- **Secrets:** secret-looking values are replaced by `[redacted]` and counted in a warning. This
  covers `password:`, `api_key =` and similar assignments, URL passwords, bearer tokens and private
  keys.

## Ingestions

`IngestionRequest` is `{content?, retryOf?}`:

- A document source needs the new `content`.
- A record source is read from ArchitectOS as it is now. Sending `content` returns
  `records_are_read_from_architectos`.
- `retryOf` names the failed run this one retries.

Each ingestion is stored as an `IngestionRun`:

- **Fields:** `{id, sourceId, trigger, status, stage, checksum, counts {documents, chunks, skipped,
  failed}, versions, indexedVersion, warnings, errors [{code, message, stage, locator}], retryOf,
  requestedByUserId, requestedAt, startedAt, completedAt}`.
- **`versions`** records the adapter, redaction, structure and chunking rules, and `chunk-max-chars`.

A run's `status` is one of:

| Status | Meaning |
|---|---|
| `completed` | A new version `n + 1` is indexed in full, and only then put in force. |
| `completed_with_warnings` | The same, with something redacted or a heading-only section skipped. |
| `unchanged` | The content is the indexed version's. Nothing is created. |
| `failed` | Nothing changed: the version in force, if any, stays. `stage` says where it stopped. Errors include `invalid_characters`, `bidirectional_override`, `line_too_long`, `nothing_to_index` and `record_deleted`. |

## A source

`KnowledgeSource` fields:

- **Identity:** `id`, `projectId`, `type`, `name`, `contentType`, `path`, `record {recordId, label,
  version}`.
- **Index:** `status`, `lifecycle`, `indexedVersion`, `indexedChecksum`.
- **Other:** `metadata`, `createdByUserId`, `createdAt`, `updatedAt`, `archivedAt`.

`status` is one of:

| Status | Meaning |
|---|---|
| `pending` | Registered, not indexed yet. |
| `indexed` | A version is in force. |
| `failed` | Never indexed. |
| `stale` | A record source whose record now reads differently from the version in force. |

Staleness is checked when the source is read and when its passages are search candidates. A stale
source is still searched, with each passage marked `stale`, until it is re-indexed. Nothing is
re-indexed automatically.

**Archived** sources are kept with their versions, passages and runs, but are never searched and
never ingested again. Their path or record can be registered again as a new source.

## Search

`SearchRequest` is `{text?, identifiers?, sourceIds?, sourceTypes?, limit?, includeStale?}`:

- `text` and `identifiers` can't both be empty: a search never asks for everything.
- `text` is at most 500 characters, and there are at most 20 identifiers.
- `limit` is 1–20, default 10. `includeStale` defaults to true.

How passages are chosen:

1. Passages that name a requested identifier (`ADR-3`, `REQ-12`, a backticked id) come first.
2. Then passages holding at least half of the query's terms, ranked by distinct terms, then
   occurrences, then shorter passages. Terms are lower-cased words with a fixed plural rule; there is
   no stemming beyond that and no synonyms.

Only the version in force of the project's active sources is searched. Semantic similarity is not
configured, and no embeddings are stored.

`RetrievalResult` is `{note, passages, insufficientEvidence, searchedSources, limitations, versions}`.
Each passage has these fields:

| Field | Contents |
|---|---|
| `citation` | `{sourceId, sourceName, sourceType, sourceVersion, documentId, chunkId, locator, reference}`. The `locator` has only the parts the source supports: `headingPath` and lines for documents, `record` and `field` for records. |
| `text` | The source's own words. |
| `method` | `identifier` or `lexical`. |
| `rank` | The order for this query; not a measure of truth. |
| `verification` | `user_provided`. |
| `recordStatus` | The ADR's or requirement's status, for record sources. |
| `stale` | Whether the source is stale. |
| `matched` | The identifiers or terms the passage matched. |
| `limitations` | Caveats about this passage. |

`limitations` says what the search couldn't do:

- semantic similarity is not configured;
- some sources aren't indexed;
- stale sources were excluded;
- no indexed passage names a requested identifier;
- more passages matched than were returned.

No passage is added to fill `limit`.

`GET …/passages/{chunkId}` returns one passage of the version in force, or of `?version=` (for
audit), with its citation.

## Audit

| Event | Recorded when |
|---|---|
| `knowledge_source.registered` | A source is registered. |
| `knowledge_source.ingested` | An ingestion ends: indexed, unchanged or failed. Includes the run, status, version, passage count and error codes. |
| `knowledge_source.archived` | A source is archived. |

Events record identifiers, types, statuses and counts only. They never include content, names, paths
or queries. Searches are not audited.
