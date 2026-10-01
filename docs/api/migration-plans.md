# Migration plans API

Migration plans of a project: a **proposal for engineering review** of how to move an architecture
from an exact source revision to an exact target — a later revision of the same architecture, or an
[evolution](evolution.md) candidate on the source revision. A plan states ordered steps with their
dependencies, risks, data migrations, downtime and compatibility questions, verification checkpoints,
rollback considerations, and the other engines' stored analyses as evidence. **Nothing here executes
a step, switches traffic, moves data or changes the architecture**, and nothing is approved
automatically: a person submits a version, and a person with `migration.approve` approves or rejects
that exact version. No score, probability, duration or volume is ever stated; an unknown value is
`null` or `unknown`.

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /projects/{projectId}/migration-plans` | `migration.plan` | `MigrationPlanRequest` | `201 MigrationPlan` | `404 architecture_not_found, architecture_revision_not_found, evolution_analysis_not_found, candidate_not_found`, `409 project_archived, architecture_archived`, `422 invalid_migration_request, validation_error` |
| `GET /projects/{projectId}/migration-plans` | `architecture.read` | | `200 {plans, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/migration-plans/{planId}` | `architecture.read` | | `200 MigrationPlan` | `404 migration_plan_not_found` |
| `GET /projects/{projectId}/migration-plans/{planId}/versions` | `architecture.read` | | `200 {versions}` | `404 migration_plan_not_found` |
| `GET /projects/{projectId}/migration-plans/{planId}/versions/{version}` | `architecture.read` | | `200 MigrationPlan` | `404 migration_plan_not_found` |
| `GET /projects/{projectId}/migration-plans/{planId}/versions/{version}/steps` | `architecture.read` | | `200 {steps, sequence}` | `404 migration_plan_not_found` |
| `GET /projects/{projectId}/migration-plans/{planId}/versions/{version}/risks` | `architecture.read` | | `200 {risks, assumptions}` | `404 migration_plan_not_found` |
| `GET /projects/{projectId}/migration-plans/{planId}/versions/{version}/checkpoints` | `architecture.read` | | `200 {checkpoints}` | `404 migration_plan_not_found` |
| `GET /projects/{projectId}/migration-plans/{planId}/versions/{version}/rollbacks` | `architecture.read` | | `200 {rollbacks}` | `404 migration_plan_not_found` |
| `POST /projects/{projectId}/migration-plans/{planId}/regenerate` | `migration.plan` | `{request?, replaceReviewed?}` | `200 {created, plan}` | `404 migration_plan_not_found` (and the create errors), `409 reviewed_migration_plan, invalid_migration_plan_transition`, `422 invalid_migration_request` |
| `POST /projects/{projectId}/migration-plans/{planId}/versions/{version}/submit` | `migration.plan` | | `200 MigrationPlan` | `404 migration_plan_not_found`, `409 invalid_migration_plan_transition, stale_migration_plan` |
| `POST /projects/{projectId}/migration-plans/{planId}/versions/{version}/approve` | `migration.approve` | `{fingerprint, comment?}` | `200 MigrationPlan` | `404 migration_plan_not_found`, `409 invalid_migration_plan_transition, stale_migration_plan, migration_plan_version_mismatch` |
| `POST /projects/{projectId}/migration-plans/{planId}/versions/{version}/reject` | `migration.approve` | `{fingerprint, comment}` | `200 MigrationPlan` | `404 migration_plan_not_found`, `409 invalid_migration_plan_transition, migration_plan_version_mismatch`, `422 invalid_migration_request` |
| `POST /projects/{projectId}/migration-plans/{planId}/versions/{version}/archive` | `migration.plan` | | `200 MigrationPlan` | `404 migration_plan_not_found`, `409 invalid_migration_plan_transition` |

On every endpoint: `404 project_not_found` outside your organizations and `403 permission_denied`
without the permission. Viewers read plans; members, admins and owners generate, regenerate, submit
and archive them (`migration.plan`); only admins and owners approve or reject (`migration.approve`).
Writes to an archived project or architecture are `409 project_archived` / `409 architecture_archived`.
`GET …/migration-plans` filters by `architectureId` and `status`, with `cursor` and `limit` (1–100,
default 50); it lists the latest version of each plan, newest first.

## The request

`MigrationPlanRequest` (camelCase, unknown fields refused): `{architectureId, sourceRevision, target,
goals?, constraints?, dataRequirements?, requirementIds?, strategy?, assumptions?, title?}`.

- `target` is either `{revision}` (a later revision of the same architecture) or `{analysisId,
  candidateId}` (a candidate of an evolution analysis of the source revision).
- `constraints`: `{downtimeAllowed (null: not stated, never assumed), maintenanceWindow, statements}`.
- `dataRequirements`: `[{elementId, statement}]`; `assumptions`: `[{key, statement}]`.
- `strategy`: a preferred strategy, followed only when its prerequisites are declared.

Refusals are `422 invalid_migration_request` with `details: {field, reason}` (and the element or
reference concerned, when there is one), e.g. a target revision that is not after the source.

## Lifecycle

- **draft**: generated or revised, not yet submitted. Only a draft can be submitted.
- **needs_information**: something required is missing (a blocking finding, or no steps); it cannot be
  reviewed as is — regenerate it with a revised request.
- **ready_for_review**: submitted by a person.
- **approved** / **rejected**: a person with `migration.approve` reviewed this exact version. The body
  names the version's `fingerprint` (64 hex characters); another fingerprint is `409
  migration_plan_version_mismatch` (`details: {version, reason}`, `version_mismatch` or
  `content_mismatch`). A rejection carries a `comment` saying why. Approval executes nothing.
- **superseded**: a later version of the plan replaced it (regeneration); kept with its review history.
- **archived**: kept for history, no longer in use.

Transitions the lifecycle does not allow are `409 invalid_migration_plan_transition` (`details: {from,
to}`). A version is **stale** when its source, target, models or evidence changed (for example the
architecture has a revision later than the plan's target); staleness is computed on read
(`freshness: {stale, reasons}`), and a stale version is shown but never approved (`409
stale_migration_plan`, `details: {reasons}`).

Regenerating creates a new version from the latest version's request, or from a revised `request`.
Replacing a version under review or approved needs `replaceReviewed: true` (otherwise `409
reviewed_migration_plan`, `details: {version, status}`). An identical result creates nothing:
`{created: false, plan}` with the latest version, and no audit entry.

## The plan

`{id (the version's id), planId, version, projectId, architectureId, title, status, source
(architectureId, revisionNumber, contentHash), target (kind, architectureId, revisionNumber,
contentHash, analysisId, candidateId), strategy, fingerprint, summary (counts only), reviews (fromStatus,
toStatus, userId, at, comment, fingerprint), createdByUserId, createdAt, freshness, request, steps,
sequence, risks, assumptions, checkpoints, rollbacks, dataMigrations, compatibility, findings, evidence,
coverage, alternatives, models, diffSummary}`. The list and history return the summary fields only.
Checkpoint statuses are planning-time statuses, never proof of runtime success; a data migration's
duration is always unevaluable (volume and throughput are not modeled). A malformed stored plan is an
internal error (`invalid_migration_plan`), never returned as a client error.

## Audit

`migration_plan.created`, `migration_plan.regenerated`, `migration_plan.submitted`,
`migration_plan.approved`, `migration_plan.rejected`, `migration_plan.archived` (resource
`migration_plan`, the plan id; identifiers and counts only: project, architecture, plan, version,
status, source revision, target kind and revision, step and finding counts — never goals, statements
or review comments), in `GET /organizations/{orgId}/audit-log`.
