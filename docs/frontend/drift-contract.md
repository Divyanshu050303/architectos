# Frontend contract: drift

The backend implements [docs/api/drift.md](../api/drift.md). The web app still uses the shape it
proposed, served by its mock API:

- `apps/web/api/drift.ts`: `GET /projects/{id}/drift` returns the latest report or
  `404 not_checked`, and `POST /projects/{id}/drift/check` returns `422 discovery_required` when no
  source is connected.
- `apps/web/schemas/drift.ts`: `DriftReportSchema` and `DriftItemSchema`.
- `apps/web/types/discovery.ts`, `apps/web/features/drift/*`, and the mock in
  `apps/web/api/mock/analysis-handlers.ts`.

The frontend alignment step adopts the backend's shapes, as decided in
[ADR-022](../adr/ADR-022-deterministic-drift-detection.md):

| Area | `apps/web` today | Backend | Resolution in the frontend step |
|---|---|---|---|
| Starting a check | `POST /drift/check` against a connected source (connector kind) | `POST /projects/{id}/drift-analyses {architectureId, baselineRevision, discoveryRunId, exclude?, label?}`: an exact revision against a stored discovery run. There are no connectors and no live scan. | Pick a revision and a discovery run (from `GET …/discovery-runs`). Explain that a new run means supplying artifacts again. |
| Report | One latest report (`checkedAt`, `source`, `architectureVersion`) | A stored, listable analysis history (`GET …/drift-analyses?architectureId=`). Each analysis has `baseline` (revision, content hash), `observed` (run, fingerprints), `status`, `compatibility[]`, `coverage`, `warnings`, `summary` and `versions`. | Show the history. Show compatibility and coverage **before** any difference. |
| Status | `drifted`, `matching`, `missing`, `unexpected` | Finding `type` (12 types, e.g. `component_added`, `component_removed`, `resource_changed`, `coverage_changed`, `comparison_incompatible`) plus `classification`: `confirmed`, `potential`, `not_comparable`, `unknown`. An analysis can be `incompatible_inputs`, with no differences. | Show type and classification separately. Never show `unknown` or `potential` as "missing". Never label an analysis "matching": `noDifferenceWithinCoverage` holds only for the inspected scope. |
| Values | `expected` and `actual` as display strings | `baselineValue` and `discoveredValue` as canonical JSON (null means absent), plus `redacted`. Each finding also has `path`, `explanation`, `limitations`, `evidence`, `locations` and `baselineReference`. | Render typed values. Show `redacted` as "changed (secret)". Link each location to its artifact path. |
| Severity | `severity` per item | None. There are counts only (`summary.types`, `summary.classifications`), with no score or ranking. | Remove severity and sort by subject. |
| Impact | — | `impact[]`: `{engine, basis, state (current, stale, missing), analysisId, revisionNumber, items}` | Show as context ("capacity reads replicas; no capacity analysis of revision 1"), never as an impact claim. |
| Following a difference | — | Drift items (`GET …/drift-items`) with `status` (`open`, `acknowledged`, `investigating`, `accepted`, `dismissed`, `resolved`, `reopened`), `history`, `links` and `artifacts` | A per-architecture list of items with their history. |
| Review | — | `POST …/drift-items/{id}/review {action, note?, link?, evidenceAnalysisId?}`. A refusal is `409 invalid_drift_review_action` with `details.reason`. | Offer only the actions the status allows. Require a note for dismiss and reopen. Resolve needs a later analysis that no longer detects the item, or a revision link. |
| Identity | — | `GET`/`POST …/architectures/{id}/identity-mappings {baselineId, discoveredKey (or null), note?}` | For `ambiguous` or unmatched nodes, offer "this is the same component", or retract it. |
| Fixing drift | — | No remediation exists. `accept` doesn't update the baseline. | Changing the architecture is a normal architecture edit; afterwards, link the revision to the item. |
| Permissions | — | `architecture.drift` (members and up) runs, reviews and confirms; viewers read. | Hide write controls for viewers. |
| Errors | `422 discovery_required`, `404 not_checked` | `422 invalid_drift_request` (`details.reason`, e.g. `no_result`), `404 drift_analysis_not_found` and `drift_item_not_found`, `409 discovery_run_in_use` (on discovery-run delete), `429 rate_limited` | Map each to a message. An empty history replaces `not_checked`. |

This message is always shown: "Drift compares what this revision states with what the supplied
artifacts declare — not the running system. Differences are reported within what was inspected; they
are not judged authorized or harmful, and nothing changes the architecture."

Unchanged and already aligned: authentication, the error envelope, project scoping
(`404 project_not_found` outside the organization) and camelCase JSON.
