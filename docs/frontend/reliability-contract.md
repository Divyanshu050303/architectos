# Frontend contract: reliability

The backend implements [docs/api/reliability.md](../api/reliability.md). The web app still uses
the shapes it proposed (`apps/web/api/reliability.ts`, `apps/web/schemas/reliability.ts`,
`apps/web/features/reliability/`), served by its mock API; the frontend alignment step adopts the
backend's. Decided in [ADR-014](../adr/ADR-014-deterministic-reliability.md):

| Area | `apps/web` today | Backend | Resolution in the frontend step |
|---|---|---|---|
| Scope | One analysis per project: `GET /projects/{id}/reliability`, `POST …/reliability/analyze` | Analyses per architecture: `POST/GET /projects/{id}/architectures/{architectureId}/reliability-analyses`, `GET …/{analysisId}`, `…/components`, `…/findings` | Analyze the selected architecture; show its latest analysis |
| Input | None | `revision`, `entries`, `objectives` (availability, recovery time, data loss, redundancy), `assumptions`, `label` | A form for entries and objectives; component inputs are edited on the architecture |
| Availability | `availability.estimated: number`, `target` | No architecture-wide value: `paths[].availability` per entry, `{value, unit: "ratio"}` decimal string or `null` with `missing` | Show each entry's estimate; "unknown" and what is missing when `null`, never 0 or 100% |
| Downtime | `monthlyDowntimeMinutes` | Not returned | If shown, derive from a known estimate (730-hour month) and label it an estimate, never guaranteed uptime |
| Target | `availability.target` | `objectives[]` with `verdict` (`satisfied`, `violated`, `not_verifiable`, `not_applicable`), `actual`, `missing`, `requirementId` | Show verdicts; `not_verifiable` is not a pass |
| Entry points | `entrypoints[{nodeId, availability}]` | `paths[]`: `entryId`, `nodeIds`, `connectionIds`, `optionalConnectionIds`, `complete`, `availability` (basis, inputs, assumptions) | Show the path and the values and assumptions used |
| Single points of failure | `singlePointsOfFailure[{nodeId, reason, dependentNodeIds, severity}]` | Findings `type: single_point_of_failure`, with `nodeIds`, `evidence` (paths, what it affects), `certainty`, `recommendation` | Filter findings by type; show `candidate` as "worth a look" |
| Critical paths, cascade risks | `criticalPaths`, `cascadeRisks`, `criticalEdgeIds` | `paths[]`, and findings (`critical_dependency_without_alternative`, `potential_correlated_failure`, `circular_dependency`, …) | Highlight `paths[].connectionIds` and findings' `connectionIds` |
| Components | None | `GET …/components`: estimates (availability, replica availability, recovery time, data-loss window), declared inputs with provenance, `missing` | A per-component panel |
| Evidence | `evidenceId` | Each estimate's model and version, formula basis, inputs and assumptions; `limitations[]` | Show where each number comes from; always show "estimate, not guaranteed uptime" |
| Version | `architectureVersion`, `analyzedAt` | `revision`, `revisionContentHash`, `completedAt`, `resultFingerprint` | Show the revision analyzed |

Unchanged and aligned: authentication, the error envelope, severities (validation's scale), and
project scoping (`404 project_not_found` outside the organization).
