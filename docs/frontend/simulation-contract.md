# Frontend contract: simulations

The backend implements [docs/api/simulations.md](../api/simulations.md). The web app still uses the
shapes it proposed, served by its mock API. They live in:
- `apps/web/api/simulations.ts`;
- `apps/web/schemas/simulation.ts`;
- `apps/web/types/simulation.ts`;
- `apps/web/hooks/use-simulation.ts`;
- `apps/web/features/simulation/`.

The frontend alignment step adopts the backend's shapes. Decided in
[ADR-017](../adr/ADR-017-deterministic-simulation.md):

| Area | `apps/web` today | Backend | Resolution in the frontend step |
|---|---|---|---|
| Scope | Per project: `POST /projects/{id}/simulations` (202, a queued job), `GET …/simulations/latest`, `GET …/simulation/scenarios` | Per architecture, synchronous: `POST/GET /projects/{id}/architectures/{architectureId}/simulations` (201), `GET …/{simulationId}`, `…/components`, `…/deltas`, `…/comparisons/{otherId}`, `GET /simulation/catalog` | Simulate the selected architecture. No job polling or `steps`: the response is the stored simulation |
| Scenario | `scenarioId` from a fixed list (`traffic_spike`, `database_failure`, `redis_failure`, `kafka_failure`, `region_failure`, `network_partition`) with `targetNodeIds` | A composed `scenario` with `workload` (`growth`, `growthRate` + `periods`, `targetRate`, `targetUtilization`), `changes` (`elementId`, `property`, `value`) and `failures` (`kind` component, connection, zone or region; `target`), typed by the catalog's nine types | A scenario builder from the catalog. The fixed presets become templates: a database or Redis failure is a component failure, a region failure a region failure. A network partition is one or more connection failures |
| Traffic | `traffic: current, peak, 2x, 10x` | `scenario.workload` plus a workload profile (`workload`) | "2x" is `growth: 2`. "Peak" and "current" come from the user's workload profile, never from defaults |
| Duration and environment | `durationMinutes`, `environment` | None, by design (no time model, no environments) | Remove them |
| Impact | `impact: low, medium, high, critical`; `cascadingFailure: none, potential, likely` | `entries[].impact` and `components[].impact`: `interrupted`, `degraded`, `tolerated`, `unknown`, `unaffected`, with `through` and `missing`; `summary.entries` counts | Show per-entry impacts. `unknown` is shown as "not declared", with what to declare. No overall level and no likelihood |
| Metrics | `metrics[{metric, before, after, unit}]` as numbers | `GET …/deltas`: `{analysis, elementId, metric, unit, baseline, scenario, difference, percentage, comparable, note}` as exact decimal strings, `null` when unknown | Render as strings; never coerce `null` to 0. Show `note` when `comparable` is false |
| Error rate and timeline | `errorRate {before, after}`, `timeline[{atSeconds, phase, nodeIds}]` | None, by design (not modeled, never invented) | Remove them. The canvas highlights `components` with `unavailable` or an impact, and `overlay.changes` |
| Status | Job status and `error` | `status`: `completed`, `partial`, `unsupported`, `failed`; `runs[]` with `state` and `reason` (`no_workload`, `no_pricing`, `not_concerned`, …); `unsupported[]` with `missing` | Show each analysis's run and why it did not run, with the inputs to add |
| Cost | None | Cost deltas (`monthly_cost`, `<line>.monthly_cost`, unit e.g. `USD/month`) when `pricing` is given | A cost section labelled as an estimate from the chosen snapshot |
| Comparison | None | `GET …/comparisons/{otherId}`: per analysis `comparable` and `reason` (`different_baseline`, `different_models`, …) and deltas | Compare two simulations side by side only where `comparable` |
| Version | `architectureVersion` | `revision`, `revisionContentHash`, `overlay` (its baseline hash), `resultFingerprint` | Show the revision simulated; the architecture itself is never changed |

Always shown: "Model-based projection of the declared architecture. It does not guarantee real-world
performance, availability, cost or failure behavior." Unchanged and aligned: authentication, the
error envelope, and project scoping (`404 project_not_found` outside the organization).
