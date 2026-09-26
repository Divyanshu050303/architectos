# Frontend contract: capacity

The backend implements [docs/api/capacity.md](../api/capacity.md). The web app still uses the shapes
it proposed (`apps/web/api/capacity.ts`, `apps/web/schemas/capacity.ts`), served by its mock API;
the frontend alignment step adopts the backend's. Decided in
[ADR-012](../adr/ADR-012-deterministic-capacity.md):

| Area | `apps/web` today | Backend | Resolution in the frontend step |
|---|---|---|---|
| Scope | One analysis per project: `GET /projects/{id}/capacity`, `POST …/capacity/analyze` | Analyses per architecture: `POST/GET /projects/{id}/architectures/{architectureId}/capacity-analyses`, `GET …/{analysisId}` | Analyze the selected architecture; show its latest analysis |
| Input | None (the backend was to infer the load) | A typed `workload` (type, peak rate with unit, payloads, read ratio, target utilization, assumptions), optional `entries`, `scenarios` | A workload form; prefill from the project's capacity requirements |
| Load | `{dailyActiveUsers, peakRps, writesPerSecond}` | `inputs.workload` (the request as stored) | Show the workload as entered |
| Utilization | `{nodeId, resource, used, limit, unit, utilization, threshold, status}` numbers | Per component `utilization[]`: `demand` and `capacity` as `{value, unit}` or `null`, `state`, `ratio`, `headroom`, `relativeHeadroom`, `headroomToTarget` (decimal strings) | Parse decimals; show "unknown" for `null`; `threshold` → the workload's `targetUtilization`; `status` from `state` and the target |
| Edges | `{edgeId, rps}` | `connections[]`: demand per connection with `resource`, `quantity`, `path`, `factors` | Label edges with the quantity and its unit |
| Bottleneck | One `{nodeId, resource, description, thresholdDailyActiveUsers}` or null | `GET …/bottlenecks`: a list with `condition`, `certainty` (`modeled`/`candidate`), `utilization`, `explanation`, `remediation`, `evidence` | List them; show candidates as "may be"; no single "the bottleneck" |
| Envelope | `maxSupportedDailyActiveUsers`, points | `summary.saturationMultiple` (+ `saturationComplete`) and `scenarios[]` | Show "the first known limit is reached at N× this workload" and whether that is complete; scenarios replace the envelope points |
| Provenance | `evidenceId` | Every estimate's `source`, `basis`, `inputs`, `missing`; `limitations`, `unsupported` | Show where each number comes from and what is missing |
| Scaling | None | `scaling[]` (replicas or cores needed, with the model's basis), `unsupportedScaling[]` | Offer "what it would take", only where given |
| Models | None | `GET /capacity/models` | Explain the models and what they need |

Unchanged and aligned: authentication, the error envelope, and project scoping (`404
project_not_found` outside the organization).
