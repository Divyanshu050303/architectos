# Frontend contract: validation

The backend implements [docs/api/validation.md](../api/validation.md). The web app still uses the
shapes it proposed (`apps/web/api/validation.ts`, `apps/web/schemas/validation.ts`), served by its
mock API; the frontend alignment step adopts the backend's. The differences, and how each resolves
(decided in [ADR-011](../adr/ADR-011-deterministic-validation.md)):

| Area | `apps/web` today | Backend | Resolution in the frontend step |
|---|---|---|---|
| Scope | One report per project: `GET /projects/{id}/validation` | Runs per architecture: `GET/POST /projects/{id}/architectures/{architectureId}/validations`, `GET …/validations/{runId}` | Validate the selected architecture; show its latest run (first item of the list) |
| Running | `POST …/validation/run` → report | `POST …/validations {revision?, profile?, rules?, parameters?, severityOverrides?}` → `201` run (completed or failed) | Post `{}` for the default; show `failed` runs with `error.message` |
| Findings | Embedded in the report | Paged: `GET …/validations/{runId}/findings?severity&category&blocking&ruleId&entityId&cursor&limit` | Load pages; filter server-side |
| Categories | `capacity, reliability, security, observability, cost` | `structure, configuration, completeness, policy, requirements` | Use the backend's categories; the others arrive with their engines |
| Health | `overall` and per-category scores 0–100 | None: `summary` counts by severity, category, blocking, rule failures, and requirement verdicts | Show counts and verdicts; no scores |
| Finding | `{id, ruleId, category, severity, title, location, whyItMatters, recommendation, nodeIds, edgeIds, evidenceIds, fixable, status}` | `{id, ruleId, ruleVersion, code, severity, category, title, explanation, remediation, entityIds, fieldPaths, expected, actual, evidence[], blocking, requirementId, policyRule}` | `whyItMatters` → `explanation`; `recommendation` → `remediation`; `nodeIds`/`edgeIds` → `entityIds` (look each id up in the IR); `location` from the entities' names; evidence inline |
| Triage | `status: open/ignored`, `PATCH …/findings/{id}` | None | Drop "ignore"; finding ids are stable across runs if triage is added later |
| Fixes | `fixable` | None | Hide until an engine proposes fixes |
| Requirements | None | `requirementResults[]`: `satisfied`, `violated`, `not_verifiable`, `not_applicable` with reasons | Add a requirements panel; `not_verifiable` is never shown as a pass |
| Limitations | None | `limitations[]`, `failures[]` | Show both next to the summary |
| Rules | None | `GET /validation/rules` | Rule descriptions and a rule picker |
| Policy | None | `GET/PUT /projects/{id}/architecture-policy` | Project settings: policy form (owners and admins) |

Unchanged and aligned: authentication, the error envelope, and project scoping (`404
project_not_found` outside the organization).
