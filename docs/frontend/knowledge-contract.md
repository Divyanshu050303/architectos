# Frontend contract: project knowledge

The backend implements [docs/api/knowledge.md](../api/knowledge.md). The web app has **no knowledge UI
today**. Its closest concept is the *evidence* mock:

- `apps/web/api/evidence.ts` (`GET /evidence/{id}`, `GET /projects/{id}/evidence`);
- `apps/web/schemas/evidence.ts` (`claim`, `kind: calculation | constraint | rule | benchmark`,
  `calculations`, `source`, `assumptions`);
- `apps/web/features/evidence/*`.

The mock describes **why an engine concluded something**: calculations and rules. Knowledge passages
are **what a document or record says**. They are different things, and the frontend step keeps them
apart, as decided in [ADR-023](../adr/ADR-023-lexical-evidence-retrieval.md):

| Area | `apps/web` today | Backend | In the frontend step |
|---|---|---|---|
| Sources | — | `GET/POST /projects/{id}/knowledge-sources`, `GET …/{sourceId}`, `POST …/{sourceId}/archive` | A per-project list of documents and records, with `status` (`pending`, `indexed`, `failed`, `stale`) and `lifecycle`. |
| Adding a document | — | `POST …/knowledge-sources {document: {path, content, type?}}`: inline text, ≤ 512 KiB, Markdown or plain text; `409 knowledge_source_exists` (`details.sourceId`) | Read the file in the browser and send it as text. Offer re-indexing when it already exists. |
| Adding a record | — | `POST …/knowledge-sources {record: {type: decision \| requirement, id}}` | Offer it from an ADR or a requirement page. |
| Re-indexing | — | `POST …/{sourceId}/ingestions {content?, retryOf?}` returns the source and its run (`completed`, `completed_with_warnings`, `unchanged`, `failed` with `errors[{code, message, stage, locator}]`) | Show the run's outcome and warnings (secrets redacted, empty sections skipped). For `stale` records, offer "read again". |
| Search | — | `POST /projects/{id}/knowledge/search {text?, identifiers?, sourceIds?, sourceTypes?, limit?, includeStale?}` returns `passages[]`, `insufficientEvidence`, `limitations`, `note` | A search box. Show each passage with its `citation.reference`, `stale` marker and `recordStatus`. Show `limitations`. When `insufficientEvidence`, say so: never "no" and never "false". |
| Ranking | — | `rank` is an order, with no score | No percentages, stars or confidence bars. |
| Passage | — | `GET …/{sourceId}/passages/{chunkId}?version=` | Link citations to the passage, and its source lines or record field. |
| Evidence mock | `claim`, `kind`, `calculations`, `assumptions` | Not this API. | Keep the evidence view for engine conclusions. Don't merge knowledge passages into it. |
| Permissions | — | `knowledge.read` (viewers) and `knowledge.manage` (members and up) | Hide register, re-index and archive controls from viewers. |

**Rendering passages:** passage text is untrusted. Render it as text, never as HTML or Markdown that
can execute or load anything.

This message is always shown with search results: "Passages are what their sources say, located
exactly — evidence, not verified facts."
