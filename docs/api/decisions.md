# Decisions API

Architecture decision records (ADR-<number> per project) drafted from an [evolution analysis](evolution.md):
the options considered, the evidence and assumptions, and **a person's decision**. The engine never
decides: a decision is proposed until a person accepts one option (with a rationale) or rejects them
all. **Accepting changes nothing in the architecture**: applying an option is a separate, authorized
change made in the architecture workflow (a new revision), which a person may afterwards link to the
decision as its resulting revision — their statement, recorded as such, never inferred or verified.

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /projects/{projectId}/decisions` | `architecture.evolve` | `{architectureId, analysisId, candidateIds?, title?}` | `201 Decision` | `404 architecture_not_found, evolution_analysis_not_found`, `409 project_archived, architecture_archived`, `422 invalid_decision, validation_error` |
| `GET /projects/{projectId}/decisions` | `architecture.read` | | `200 {decisions, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/decisions/{decisionId}` | `architecture.read` | | `200 Decision` | `404 decision_not_found` |
| `GET /projects/{projectId}/decisions/{decisionId}/document` | `architecture.read` | | `200 {reference, markdown}` | `404 decision_not_found` |
| `POST /projects/{projectId}/decisions/{decisionId}/accept` | `architecture.evolve` | `{candidateId, rationale}` | `200 Decision` | `404 decision_not_found`, `409 invalid_decision_transition`, `422 invalid_decision` |
| `POST /projects/{projectId}/decisions/{decisionId}/reject` | `architecture.evolve` | `{rationale}` | `200 Decision` | `404 decision_not_found`, `409 invalid_decision_transition` |
| `POST /projects/{projectId}/decisions/{decisionId}/supersede` | `architecture.evolve` | `{byDecisionId}` | `200 Decision` | `404 decision_not_found`, `409 invalid_decision_transition`, `422 invalid_decision` |
| `POST /projects/{projectId}/decisions/{decisionId}/resulting-revision` | `architecture.evolve` | `{revision}` | `200 Decision` | `404 decision_not_found, architecture_revision_not_found`, `409 invalid_decision_transition`, `422 invalid_decision` |

On every endpoint: `404 project_not_found` outside your organizations and `403 permission_denied`
without the permission. Viewers read decisions; members, admins and owners draft and decide.

## Lifecycle

- **proposed**: drafted from an analysis, with the analysis's candidates as options (all of them, or
  `candidateIds`). Each option is a snapshot of the candidate by its stable id: its title, category,
  changes, validation state, goals and direction per dimension. The context states that the options
  are alternatives and the engine prefers none. The ADR number is allocated under the project's
  exclusive lock.
- **accepted**: a person chose one option (`candidateId`), with a `rationale`. An option validation
  found `invalid` or `unsupported` cannot be accepted (`422 invalid_decision`, `reason:
  option_invalid`).
- **rejected**: a person rejected every option, with a `rationale`.
- **superseded**: an accepted decision replaced by another accepted decision of the same project
  (`byDecisionId`; not itself).
- **resulting revision**: on an accepted decision, once: a revision of its architecture after the
  baseline (`reason: not_after_the_baseline` otherwise), stored with its content hash, who linked it
  and when.

Transitions that the lifecycle does not allow are `409 invalid_decision_transition` (`details: {from,
to}`). Refusals are `422 invalid_decision` with `details: {field, reason}` (e.g. `chosen_option` /
`not_an_option`, `rationale` / `invalid_text`, `by_decision_id` / `not_accepted`, `candidate_ids` /
`not_a_candidate`).

## The decision

`{id, projectId, architectureId, number, reference (ADR-n), title, status, context, options, source
(analysisId, baseline, modelVersion), goals, evidence, assumptions, relatedElementIds, chosenOption,
rationale, decidedByUserId, decidedAt, supersededBy, resultingRevision (number, contentHash,
linkedByUserId, linkedAt), createdByUserId, createdAt}`. `GET …/document` renders it as a Markdown
ADR: status, context, options with their trade-off tables, the decision, consequences, evidence,
assumptions and the resulting revision.

## Audit

`decision.proposed`, `decision.accepted`, `decision.rejected`, `decision.superseded`,
`decision.revision_linked` (the decision reference, status, option counts, the chosen option and
linked revision when there are), in `GET /organizations/{orgId}/audit-log`.
