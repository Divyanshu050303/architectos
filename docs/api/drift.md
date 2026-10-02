# Drift API

Drift analyses of a project. An analysis compares **one exact architecture revision** (the baseline)
with **one stored discovery run** (the observed state) and reports what differs. It does not scan
anything live: the observed state is what the run's artifacts **declare** — a desired state, not the
running system.

What was not inspected is **unknown, never absent**. A baseline element is reported removed only when
the artifact it was discovered from was read completely by the run. There is **no drift score**:
findings are classified and counted.

Drift is reported, never remediated. Neither running an analysis nor reviewing an item changes the
architecture, its revisions or the discovery run.

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /projects/{projectId}/drift-analyses` | `architecture.drift` | `DriftAnalysisRequest` | `201 DriftAnalysis` | `404 architecture_not_found, architecture_revision_not_found, discovery_run_not_found`, `409 project_archived`, `422 invalid_drift_request, validation_error`, `429 rate_limited` |
| `GET /projects/{projectId}/drift-analyses` | `architecture.read` | | `200 {analyses, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/drift-analyses/{driftAnalysisId}` | `architecture.read` | | `200 DriftAnalysis` | `404 drift_analysis_not_found` |
| `GET /projects/{projectId}/drift-analyses/{driftAnalysisId}/findings` | `architecture.read` | | `200 {driftAnalysisId, findings}` | `404 drift_analysis_not_found` |
| `GET /projects/{projectId}/drift-analyses/{driftAnalysisId}/findings/{findingId}` | `architecture.read` | | `200 DriftFinding` | `404 drift_analysis_not_found` |
| `GET /projects/{projectId}/drift-items` | `architecture.read` | | `200 {items, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/drift-items/{itemId}` | `architecture.read` | | `200 DriftItem` | `404 drift_item_not_found` |
| `POST /projects/{projectId}/drift-items/{itemId}/review` | `architecture.drift` | `ReviewRequest` | `200 DriftItem` | `404 drift_item_not_found, drift_analysis_not_found`, `409 invalid_drift_review_action, project_archived` |
| `GET /projects/{projectId}/architectures/{architectureId}/identity-mappings` | `architecture.read` | | `200 {architectureId, mappings}` | `404 architecture_not_found` |
| `POST /projects/{projectId}/architectures/{architectureId}/identity-mappings` | `architecture.drift` | `IdentityMappingRequest` | `201 IdentityMapping` | `404 architecture_not_found`, `409 project_archived`, `422 invalid_drift_request` |

Every endpoint can also return:

- `404 project_not_found` when the project is outside your organizations.
- `403 permission_denied` when you lack the permission.

An analysis or item of another project or organization returns `404 drift_analysis_not_found` or
`404 drift_item_not_found`, which can't be told apart from one that doesn't exist. A finding id that
isn't part of the analysis also returns `404 drift_analysis_not_found`.

Viewers can read analyses, items and mappings. Members, admins and owners can run analyses, review
items and confirm identities (`architecture.drift`).

The two listings, `GET …/drift-analyses` and `GET …/drift-items`, both take `cursor` and `limit`
(1–100, default 50):

| Listing | Filters | Order |
|---|---|---|
| `GET …/drift-analyses` | `architectureId` (that architecture's drift history), `status` | Newest first |
| `GET …/drift-items` | `architectureId`, `status` (review status) | Creation order |

## Running an analysis

`DriftAnalysisRequest` (camelCase; unknown fields are refused):
`{architectureId, baselineRevision, discoveryRunId, policy?, exclude?, label?}`.

- **`baselineRevision`** is the exact revision that is compared, not "the latest". Its content hash
  is recorded with the analysis.
- **`discoveryRunId`** is a run of this project that has a result. A failed run returns
  `422 invalid_drift_request` with `details.reason: no_result`. A run that any analysis compared can't
  be deleted (`409 discovery_run_in_use`).
- **`policy`** can only be `default`.
- **`exclude`** lists baseline element ids or discovery keys to leave out (at most 500).

Running is synchronous. The analysis is stored with one of these statuses:

| Status | When |
|---|---|
| `completed` | The comparison ran with nothing to qualify. |
| `completed_with_warnings` | Coverage has limits, or there are compatibility warnings, or some findings are potential, not comparable or unknown. |
| `incompatible_inputs` | The inputs can't be compared reliably. There are no findings, only the reason. |
| `failed` | The engine refused its own result. |

A schema or extractor change is never reported as an architecture change.

## An analysis

`DriftAnalysis` has these fields:

- **Request:** `id`, `projectId`, `architectureId`, `baselineRevision`, `discoveryRunId`, `policy`,
  `exclude`, `label`, `status`, `requestedByUserId`, `requestedAt`, `completedAt`.
- **Outcome:** `summary`, `fingerprint`, `error`, `note`.
- **What was compared:** `baseline`, `observed`, `compatibility`, `coverage`, `warnings`, `versions`.
- **`findings`:** the number of findings. `GET …/findings` returns the findings themselves.

The parts of an analysis:

- **`baseline`** is `{architectureId, revisionNumber, contentHash, schemaVersion}`.
- **`observed`** is `{discoveryRunId, resultFingerprint, sourcesFingerprint, resultVersion,
  extractors}`.
- **`compatibility`** has one entry per dimension, each with an outcome from `compatible`,
  `compatible_with_warnings`, `partially_comparable`, `cannot_determine` or `incompatible` and a
  message. The dimensions are `ir_schema`, `discovery_result`, `extractor_versions`, `source_types`,
  `source_coverage`, `artifact_coverage`, `identity` and `freshness`.
- **`coverage`** lists the artifacts read completely (`inspected`), partly (`partial`) or not at all
  (`unread`). It also counts unsupported constructs, unresolved entities and relationships, and
  errors. `complete` is true only when nothing is missing.
- **`summary`** has counts only: `{compatibility, findings, types, classifications,
  noDifferenceWithinCoverage}`. `noDifferenceWithinCoverage` never means "no drift": it holds only
  within what was inspected.

The stored result is verified against its fingerprint whenever it is read.

## Findings

`DriftFinding` fields:

- **Identity:** `id`, `itemKey`, `type`, `classification`, `element`, `subject`, `path`.
- **Explanation:** `explanation`, `rule`.
- **Matching:** `baselineId`, `discoveredKey`, `match`.
- **Values:** `baselineValue`, `discoveredValue`, `redacted`.
- **Where it came from:** `evidence`, `locations`, `baselineReference`, `limitations`.
- **Context:** `impact`, `references`.

### Ids

- **`id`** is stable: the same difference gets the same id in every analysis.
- **`itemKey`** identifies the drift item the finding belongs to.

### Types

| Group | Types |
|---|---|
| Components | `component_added`, `component_removed`, `component_modified` |
| Connections | `connection_added`, `connection_removed`, `connection_modified` |
| Properties | `configuration_changed`, `resource_changed` (replicas, CPU, memory, storage), `mapping_changed` (catalog) |
| Comparison | `unresolved_difference`, `coverage_changed`, `comparison_incompatible` |

### Classifications

| Classification | Meaning |
|---|---|
| `confirmed` | Comparable baseline and discovery evidence supports the finding. |
| `potential` | The evidence suggests it, but identity or coverage is incomplete. |
| `not_comparable` | Differences in the inputs prevent a reliable comparison. |
| `unknown` | The evidence is insufficient. |

### Matching

`match` is one of `same_id`, `confirmed_mapping`, `signature` (a connection matched by endpoints and
kind), `ambiguous` or `unmatched`. Nothing is ever matched by name.

### Values

- Secrets are never stored or returned. `redacted: true` says that a value changed, and both values
  are `null`.
- `locations` are source references in the form `path#document:pointer`.
- `evidence` lists discovery finding ids.

### Impact

`impact` gives context from the other engines' stored analyses of the baseline revision. Each entry
is `{engine, basis, state, analysisId, revisionNumber, items}`. `state` is one of:

| State | Meaning |
|---|---|
| `current` | The analysis is of the baseline's exact content. |
| `stale` | The analysis is of other content and shouldn't be relied on. |
| `missing` | No analysis is stored. |

Nothing is recomputed for the discovered state.

## Drift items and review

Findings are folded into **drift items**. An item is one difference of one architecture, followed
across analyses and keyed by `itemKey`. Each analysis that finds the difference again appends a
`detected` event, and it reopens an item that was resolved.

`DriftItem` fields:

- **Identity:** `id`, `projectId`, `architectureId`, `key`, `element`, `subject`, `path`.
- **Latest finding:** `type`, `classification`.
- **Analyses:** `firstAnalysisId`, `lastAnalysisId`.
- **Review:** `status`, `history`, `links`, `artifacts`.

`artifacts` lists where the item's findings were read. A resolution must inspect them.

`ReviewRequest`: `{action, note?, link?: {kind, target, architectureId?}, evidenceAnalysisId?}`.

| Action | Moves the status to | Needs |
|---|---|---|
| `acknowledge` | `acknowledged` | |
| `investigate` | `investigating` | |
| `accept` | `accepted` (expected; the baseline isn't updated) | |
| `dismiss` | `dismissed` | a `note` giving the reason |
| `resolve` | `resolved` | `evidenceAnalysisId` (see below) or a `revision` link |
| `reopen` | `reopened` | a `note` |
| `note` | unchanged | a `note` |
| `link` | unchanged | a `link` |

For `resolve`, `evidenceAnalysisId` must be a drift analysis of the same architecture. That analysis
must have inspected the item's artifacts and must no longer detect the item.

A `link` points to a `decision`, `migration_plan`, `evolution_analysis` or `revision` (with
`architectureId`) **of this project**. A link to another project's record is
`link_target_not_found`.

A refused action returns `409 invalid_drift_review_action` with `details: {action, status, reason}`.
The reasons are:

| Reason | Why |
|---|---|
| `not_a_review_action` | The action is `detected`, which only analyses record. |
| `not_allowed_in_status` | The item's current status doesn't allow the action. |
| `note_required` | The action needs a `note`. |
| `invalid_note` | The `note` is empty or too long. |
| `link_required` | `link` was sent without a link. |
| `link_target_not_found` | The linked record doesn't exist in this project. |
| `evidence_required` | `resolve` was sent without evidence or a revision link. |
| `history_full` | The item's history has reached its limit. |
| `another_architecture` | The evidence analysis is of another architecture. |
| `not_compared` | The evidence analysis didn't compare anything. |
| `still_detected` | The evidence analysis still detects the item. |
| `outside_coverage` | The evidence analysis didn't inspect the item's artifacts. |

Every event is kept, and the history only grows.

## Identity mappings

`IdentityMappingRequest`: `{baselineId, discoveredKey, note?}`.

A mapping states that a node of the architecture's current revision and a discovered entity are the
same. A `discoveredKey` of `null` retracts the mapping. A `baselineId` that isn't a node returns
`422 invalid_drift_request` with `details.reason: not_a_node`.

Mappings are appended, never edited. For each node, the latest mapping applies. Later analyses use
them for matching only; the architecture isn't changed.

## Storage

- **Analyses and identity mappings** are append-only. Database triggers refuse any update, deletion
  or truncation.
- **Drift items:**
  - An item's identity never changes, and an item is never deleted.
  - Its history only grows.
  - Only its latest detection, review status, links and artifacts change.

## Audit

Each event is recorded with identifiers, statuses and counts only. Notes, values and names are never
recorded.

| Event | Recorded when |
|---|---|
| `drift_analysis.created` | An analysis runs. Includes the items opened and the items detected again. |
| `drift_item.reviewed` | A review action is accepted. Includes the action, the previous and new status, the link kind and the evidence analysis. |
| `drift_identity.confirmed` | A mapping is confirmed or retracted. Says whether it was a retraction. |
