# Frontend contract: architecture agent

The backend is in place (`docs/api/architecture-agent.md`). **The web app is not integrated yet.**
`apps/web/api/proposals.ts` and `apps/web/schemas/proposals.ts` describe an earlier, proposed
contract that the backend does not implement. This page lists what changes when the frontend is
aligned. Nothing here has been built or verified in the web app.

## Endpoints

| `apps/web/api/proposals.ts` today | Backend | Change |
|---|---|---|
| `POST /projects/{id}/proposals` `{prompt, baseVersion, selectedNodeIds}` | `POST /projects/{id}/architecture-agent-runs` `{requirementSetId, objective, constraints, preferences, exclusions, context, base, budget}` | A run is made against a **requirement set**, which is required. `base` is `{architectureId, revisionNumber}`. There are no selected nodes. |
| `POST /projects/{id}/proposals/stream` (NDJSON) | Not implemented | Runs are synchronous and bounded (90 s by default). Show one honest "Designing…" step, as `createWithoutStreaming` already does. There is no partial or streamed text. |
| — | `GET /projects/{id}/architecture-agent-runs[/{runId}]` | New: list and read runs. |
| — | `POST .../{runId}/answers` `{answers: [{questionId, answer}]}` | New: the clarification flow. |
| — | `POST .../{runId}/cancel` | New: abandon a run that is waiting for answers. |
| `POST /proposals/{id}/apply` `{baseVersion}` → `Architecture` | `POST .../{runId}/accept` `{candidateContentHash, name?}` → `{run, architectureId, revisionNumber, createdArchitecture}` | Name the reviewed candidate by its `contentHash`; the base comes from the run. Then fetch the architecture. `409 architecture_version_conflict` means the base moved on. `409 agent_candidate_not_acceptable` carries `details.reason`. |
| `POST /proposals/{id}/reject` | `POST .../{runId}/reject` `{reason}` | A reason is required. |
| `POST /projects/{id}/findings/{findingId}/fix` | Not implemented | Out of scope here: there is no automatic remediation. |

## The `Proposal` shape against an `AgentRun`

| `ProposalSchema` field | What to show instead |
|---|---|
| `kind: "change" \| "answer"` | Always a design: a run never only answers. |
| `recommendation`, `reason` | `proposal.summary`, `proposal.decisions[]` (choice, rationale, alternatives, trade-offs) and `proposal.risks`. |
| `changes[]` (a diff) | For a new architecture, `candidate.architecture` (canonical IR). For an iteration, compare `candidate.architecture` with the base revision on the client, display only. After acceptance, the revision history shows the stored change. |
| `evidenceIds` | `candidate.evidence[]` `{chunkId, sourceId, sourceVersion, reference}`, opened with `GET /knowledge-sources/{sourceId}/passages/{chunkId}`. Every `retrieved` claim cites at least one. |
| `impact[]` `{metric, before, after, unit}` | **Not produced.** Numbers come only from engines. Show each `reports[]` entry with its status. Capacity, cost and simulation are `not_evaluated`: show the stated reason and offer to run those analyses on the accepted architecture. Never invent before or after values. |
| `cost` | **Unavailable**: `usage.cost` is always `null` (no pricing is configured), and `reports[engine=cost]` is `not_evaluated`. Show "Cost not estimated", never `0`. |
| `validation` `{summary, newFindings, resolvedFindingIds, passes}` | `reports[engine=validation]`: `findings[]` (rule, severity, message, elements) and `summary` (counts, including `blocking`). `passes` corresponds to `acceptability.acceptable`, and the reason is shown when it is false. There are no `resolvedFindingIds`. |
| `status: pending \| applied \| rejected` | `status`: `awaiting_clarification`, `candidate_ready`, `failed`, `cancelled`, `accepted`, `rejected`. |

## What the UI must show, and how

- **Run creation and progress:** the run is returned when the pass ends. Show its `stage`, and
  `history[]` for what happened.
- **Clarification:** when `status = awaiting_clarification`, show `questions[]` where `blocking` is
  true as required answers, and non-blocking ones as optional. Submit them to `/answers`. Each `kind`
  (`conflict`, `missing_concern`, `ambiguity`, `unbounded`, `proposer`) can have its own wording, but
  the question text comes from the backend.
- **Candidate:** render `candidate.architecture` with the existing IR viewer. Mark every element as
  an unverified AI proposal (its provenance is `llm_proposal` with the model's stated confidence).
  Never present that confidence as a quality score.
- **Assumptions and unknowns:** `proposal.claims[]` grouped by `basis`: `proposed`, `assumption`
  (also the IR's assumptions), `retrieved` (with citations), `unknown` and `unsupported`. Also show
  `candidate.uncoveredRequirements` and `limitations[]`. They are never collapsed into one score.
- **Failures:** `failure` `{code, message, stage}`. Show `message` as written, because it never
  contains provider details. Show `rejections[]` `{code, path, detail}` when the proposal was
  refused.
- **Decision:** offer Accept only when `acceptability.acceptable` is true. Accept with
  `candidate.contentHash`, and offer Reject with a reason.

Don't re-implement backend rules in the UI: gap detection, validation, acceptability and limits are
the backend's.
