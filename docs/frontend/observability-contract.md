# Frontend contract: observability

The backend implements [docs/api/observability.md](../api/observability.md). The web app still uses
the shapes it proposed (`apps/web/api/observability.ts`, `apps/web/schemas/observability.ts`,
`apps/web/features/observability/`, the inspector's `ObservabilityTab`), served by its mock API; the
frontend alignment step adopts the backend's. Decided in
[ADR-016](../adr/ADR-016-deterministic-observability.md):

| Area | `apps/web` today | Backend | Resolution in the frontend step |
|---|---|---|---|
| Scope | One analysis per project: `GET /projects/{id}/observability`, `POST …/observability/analyze` | Analyses per architecture: `POST/GET /projects/{id}/architectures/{architectureId}/observability-analyses`, `GET …/{analysisId}`, `…/components`, `…/findings` | Analyze the selected architecture; show its latest analysis |
| Input | None | `revision`, `scope`, `analyzers`, `requirementIds`, `assumptions`, `label`; the policy is the project's (`PUT …/architecture-policy`) | A run form; observability properties are edited on the architecture, policy on the project |
| Score | `score: 0–100` | None, by design | Remove the score; show coverage counts per dimension, findings by severity and basis, and the status |
| Coverage | `coverage[{nodeId, metrics, logs, traces, alerts, dashboards: boolean}]` | `GET …/components`: `coverage` per dimension (`logging`, `metrics`, `tracing`, `healthChecks`, `alerting`) as `modeled`, `partial`, `absent`, `unknown`, `unsupported`; `criticality`; declared facts; `missing` | Five states, not booleans: show "not declared" for `unknown` and "third party" for `unsupported`; dashboards are not modeled |
| SLOs | `slos[{target, current, errorBudgetRemaining, window}]` | `checks[]` with conditions `objective_measurable` and `objective_alerted` per objective requirement; `summary.objectives` counts | Show whether each objective has a modeled indicator and alert, labelled "traceable, not measured"; drop `current` and `errorBudgetRemaining` (they need runtime telemetry, which is never read or invented) |
| Gaps | `gaps[{nodeId, missing: signals, recommendation}]` | Findings with `type`, `basis`, `dimension`, `evidence`, `missing` (properties to declare), `recommendation` | A findings view in priority order (`summary.priorities`); never label unknowns as passed or failed |
| Collection | None | `summary.collection` per signal; `telemetry_not_collected` findings | Show declared-versus-collected counts per signal |
| Requirements and policy | None | `checks[]` with verdicts, the `mapping` used, `requirementId` / `policyRule` | A checks view; `not_verifiable` is not a pass |
| Version | `architectureVersion`, `analyzedAt` | `revision`, `revisionContentHash`, `completedAt`, `resultFingerprint` | Show the revision analyzed |

Always shown: "Architecture-level analysis of declared configuration; no live telemetry is read, and
SLO attainment is not calculated." Unchanged and aligned: authentication, the error envelope,
severities (validation's scale), and project scoping (`404 project_not_found` outside the
organization).
