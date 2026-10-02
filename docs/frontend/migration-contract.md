# Frontend contract: migration plans

The backend implements [docs/api/migration-plans.md](../api/migration-plans.md). The web app still
uses the shape it proposed, served by its mock API:
- `apps/web/api/migrations.ts` (`GET /projects/{id}/migrations`, `GET /migrations/{id}`);
- `apps/web/schemas/evolution.ts` (`MigrationPlanSchema`, `MigrationStepSchema`);
- `apps/web/types/evolution.ts` (`MigrationPlan`), and the evolution feature's migration view.

The frontend alignment step adopts the backend's shapes. Decided in
[ADR-020](../adr/ADR-020-deterministic-migration-planning.md):

| Area | `apps/web` today | Backend | Resolution in the frontend step |
|---|---|---|---|
| Scope | Per project, between evolution `stages` (`fromStageId`, `toStageId`) | Per project, between an exact source revision and a later revision of the same architecture, or an evolution candidate on the source revision: `POST/GET /projects/{id}/migration-plans` | Start a plan from an architecture's history (two revisions) or from an evolution candidate |
| Duration | `estimatedDuration` per plan and per step ("2 days") | None, by design: durations and volumes are not modeled; data migrations state `duration: "unevaluable"` and what is missing | Remove every duration; show what is missing instead |
| Risk | `risk` / `overallRisk`: low, medium, high | `risks[]` with `category`, `status` (`confirmed`, `potential` with `preconditions`, `unknown`), `impact`, `mitigation`, `traces`; no level, no score | Show the risk list by category and status; never derive a level |
| Step status | `pending`, `in_progress`, `done` | None: steps are planned, never executed; the plan's `status` is a review status (`draft`, `needs_information`, `ready_for_review`, `approved`, `rejected`, `superseded`, `archived`) | Remove step progress; show the review status |
| Order | `order` per step, `dependsOn` | `dependsOn` (step ids) and `sequence[]` (numbered stages, `parallel` only when explicit) | Render the sequence; never run steps in parallel unless `parallel` is true |
| Rollback | `rollback` text per step, `rollbackPlan` | `rollbacks[]` per high-impact step: `reversibility` (including `irreversible` and `unknown`), `action`, `retained`, `preconditions`, `consistency`, `limitations` | Show limitations and irreversible steps prominently; an action is a proposal, not a button |
| Downtime | None | Per step `downtime` (`known_downtime`, `potential_downtime` with `downtimeNote`, `modeled_online`, `unknown`), `traffic`, `availability` | Show each step's downtime; never present `unknown` as online |
| Verification | None | `checkpoints[]` with `status` (`pass`, `fail`, `warning`, `not_run`, `cannot_evaluate`, `manual_verification_required`), `basis`, `blocking`, `evidence` | Show blocking checkpoints first; label that a planning-time pass is not proof of runtime success |
| Evidence | None | `evidence[]`, `coverage[]` per engine and side (`current`, `stale`, `missing`, `unsupported`), `findings[]` | Show which analyses were used and which to run |
| Review | None | `…/versions/{n}/submit`, `…/approve` and `…/reject` with the version's `fingerprint`, `…/archive`, `…/regenerate`, `…/versions` (history), `freshness` | Approve only with `migration.approve` and the fingerprint shown; disable review when `freshness.stale` |

Always shown: "Migration plans are proposals requiring engineering review and authorization. Nothing
is executed and the architecture is not changed." Unchanged and aligned: authentication, the error
envelope, and project scoping (`404 project_not_found` outside the organization).
