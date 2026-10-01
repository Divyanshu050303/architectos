# Frontend contract: evolution and decisions

The backend implements [docs/api/evolution.md](../api/evolution.md) and
[docs/api/decisions.md](../api/decisions.md). The web app still uses the shapes it proposed, served
by its mock API. They live in:
- `apps/web/api/evolution.ts`, `apps/web/api/migrations.ts`, `apps/web/api/decisions.ts`;
- `apps/web/schemas/evolution.ts`, `apps/web/schemas/decisions.ts`;
- `apps/web/types/evolution.ts`, `apps/web/types/architecture.ts` (`Decision`, `DecisionInput`);
- `apps/web/hooks/use-evolution.ts`, `apps/web/hooks/use-decisions.ts`;
- `apps/web/features/evolution/`, `apps/web/features/decisions/`.

The frontend alignment step adopts the backend's shapes. Decided in
[ADR-018](../adr/ADR-018-deterministic-evolution.md):

| Area | `apps/web` today | Backend | Resolution in the frontend step |
|---|---|---|---|
| Scope | Per project: `GET /projects/{id}/evolution` (a roadmap), `GET …/evolution/compare?from=&to=` | Per architecture revision, synchronous: `POST/GET /projects/{id}/architectures/{architectureId}/evolution-analyses` (201), `GET …/{analysisId}`, `…/candidates`, `…/candidates/{candidateId}`, `…/alternatives`, `GET /evolution/catalog` | Analyze the selected architecture against goals; the response is the stored analysis |
| Input | None (the roadmap is served) | `goals` typed by the catalog (`increase_workload`, `cost_ceiling`, `availability_objective`, `recovery_objective`, `address_finding`, `observability_coverage`, `satisfy_requirement`) with explicit targets and units; `evidence`, `constraints`, `scope`, `assumptions` | A goal builder from the catalog. "Address this finding" can link from a security, reliability or observability finding by its id |
| Stages | `stages[]` (`V1`, `V2`, …) with `dailyActiveUsers`, `maxSupportedDailyActiveUsers`, `status` past/current/planned | None, by design (no growth model; users are not a modeled quantity) | Remove the timeline of stages. Past revisions come from the architecture history; the future is candidates, not stages |
| Changes | `changes[{kind: add, remove, change, description}]` | `candidates[].changes[{elementId, property, value}]` — configuration of existing elements only; the overlay's `changes` (`before -> after`) and structural `diff` | Show each candidate's changes and diff on the canvas. "add"/"remove" changes do not exist: structural ideas arrive as `structural_consideration` findings |
| Cost and risk | `monthlyCost`, `risk: low, medium, high`, `overallRisk` | Per-candidate `impacts` (engine, state, deltas as exact decimal strings, `null` when unknown) and `consequences` (`dimension`, `direction`, `basis`, `statement`) | Show the consequence table per candidate. Never coerce `unknown` or `null` to 0; no risk level, no score |
| Comparison | Compare two stages | `GET …/alternatives`: for each goal, the candidates side by side with their direction per dimension; canonical order, not a ranking | Side-by-side table; never highlight a "best" option |
| Evidence | `trigger` (free text) | `evidence[]` with `state` (`current`, `stale`, `missing`); `findings[]` (`stale_evidence`, `missing_evidence`, `goal_not_evaluable`, …) with `missing` | Show which analyses were used and which to run; a stale analysis is labelled, not hidden |
| Status | None | `status`: `completed`, `partial`, `insufficient_evidence`, `failed`; `validation` per candidate (`valid`, `invalid`, `not_evaluable`, `unsupported`) with `validationNotes` | Label invalid and unsupported candidates; they cannot be chosen |
| Migrations | `GET /projects/{id}/migrations`, `GET /migrations/{id}` with steps, durations and rollback | Migration plans between exact revisions: see [migration-contract.md](migration-contract.md) | Adopt the migration plan contract |
| Decisions | `POST /projects/{id}/decisions` with free-text `context`, `decision`, `consequences`, `relatedNodeIds`, `sourceFindingId`; `status` set by the client | `POST …/decisions` `{architectureId, analysisId, candidateIds?, title?}` drafts a `proposed` ADR from an analysis; `…/accept` `{candidateId, rationale}`, `…/reject`, `…/supersede`, `…/resulting-revision`; `GET …/document` (Markdown) | Draft from an analysis, then accept or reject with a rationale. The status changes only through those actions. `number` and `reference` (ADR-n) come from the server |
| Version | `architectureVersion` | `revision`, `revisionContentHash`, `baseline` on every candidate, `resultFingerprint` | Show the revision analyzed; the architecture itself is never changed |

Always shown: "Candidates are proposals requiring engineering review and authorization. Nothing is
applied to the architecture." Accepting a decision changes nothing: applying the chosen change is an
edit in the architecture workflow. Unchanged and aligned: authentication, the error envelope, and
project scoping (`404 project_not_found` outside the organization).
