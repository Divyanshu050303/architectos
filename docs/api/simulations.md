# Simulations API

Deterministic what-if simulation of an architecture revision: a **scenario** (a workload change,
configuration changes, failures) is applied to an in-memory copy of the revision — the **overlay** —
and evaluated next to the unchanged **baseline** by the Capacity, Reliability and Cost Engines, as
the scenario concerns them and their inputs allow. The same revision, request, requirements and
pricing snapshot always give the same result and `resultFingerprint`.

**Results are model-based projections** of the architecture as declared. They do not guarantee
real-world performance, availability, cost or failure behavior, and no telemetry is read. What an
engine cannot decide stays **unknown** (`null`), never 0; an analysis whose inputs are missing is
**unsupported**, with the reason, never estimated. The architecture itself is never changed.

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /projects/{projectId}/architectures/{architectureId}/simulations` | `architecture.analyze` | `{scenario, revision?, analyses?, workload?, entries?, pricing?, assumptions?, label?}` | `201 Simulation` | `404 architecture_revision_not_found, pricing_snapshot_not_found`, `409 project_archived, architecture_archived`, `422 invalid_simulation_request, invalid_workload_profile, invalid_capacity_quantity, validation_error`, `429 rate_limited` |
| `GET /projects/{projectId}/architectures/{architectureId}/simulations` | `architecture.read` | | `200 {simulations, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/architectures/{architectureId}/simulations/{simulationId}` | `architecture.read` | | `200 Simulation` | `404 simulation_not_found` |
| `GET /projects/{projectId}/architectures/{architectureId}/simulations/{simulationId}/components` | `architecture.read` | | `200 {components, nextCursor}` | `404 simulation_not_found`, `422 invalid_cursor` |
| `GET /projects/{projectId}/architectures/{architectureId}/simulations/{simulationId}/deltas` | `architecture.read` | | `200 {deltas, nextCursor}` | `404 simulation_not_found`, `422 invalid_cursor` |
| `GET /projects/{projectId}/architectures/{architectureId}/simulations/{simulationId}/comparisons/{otherSimulationId}` | `architecture.read` | | `200 SimulationComparison` | `404 simulation_not_found`, `422 invalid_simulation_request` |
| `GET /simulation/catalog` | signed in | | `200 {scenarioTypes, evaluators, limits}` | |

On every project endpoint: `404 project_not_found` / `architecture_not_found` outside your
organizations (existence is never revealed) and `403 permission_denied` without the permission.
Viewers read simulations; members, admins and owners also run them.

## Running a simulation

Synchronous: simulated and stored in the request, append-only (a simulation is never changed or
deleted). Rate limit: 120 per user per hour. Archived projects and architectures refuse new
simulations (`409`). A request the engine refuses stores nothing.

```json
{
  "scenario": {
    "name": "Black Friday with a zone down",
    "workload": {"growth": "3"},
    "changes": [{"elementId": "api", "property": "replicas", "value": 6}],
    "failures": [{"kind": "zone", "target": "eu-west-1a"}]
  },
  "workload": {"...": "a capacity workload profile, as for capacity analyses"},
  "pricing": {"snapshotId": "01a0de60-a784-7244-8ecc-62afb5352f47"},
  "assumptions": [{"key": "peak", "statement": "Peak lasts two hours."}],
  "label": "Launch readiness"
}
```

- `scenario.workload`: one of `growth` (a multiplier), `growthRate` with `periods` (compound),
  `targetRate` (a rate with its unit) or `targetUtilization`. It scales the `workload` profile below.
- `scenario.changes` (at most 50): a node or connection property with its new value (units are in
  the property name; `null` clears it). Only properties an engine reads are accepted; every change is
  checked by the architecture's own property rules and applied as an IR command.
- `scenario.failures` (at most 50): `component`, `connection`, `zone` or `region` with its target.
  A failure marks elements unavailable; nothing is removed from the overlay.
- `revision`: default the current revision. `analyses`: `capacity`, `reliability`, `cost`; default
  every analysis the scenario concerns.
- `workload` (a [capacity](capacity.md) workload profile) is needed by capacity and by cost usage;
  `entries` (where requests enter; default the clients) by capacity and reliability.
- `pricing`: an organization [pricing snapshot](pricing.md), used for both sides, with
  `pricingDate` (default today) and `operatingHoursPerMonth` (default 730). The currency and provider
  are the project's settings.
- `assumptions` are recorded with the simulation, never computed with.

Refusals are `422 invalid_simulation_request` with `details: {field, reason, elementId?, limit?}`,
for example `scenario.changes.element_id` / `unknown_element`, `scenario.changes.property` /
`not_simulated`, `scenario.changes.value` / `invalid_value`, `scenario.changes` /
`changed_and_unavailable`, `scenario.failures.target` / `unknown_zone`, `scenario.failures` /
`too_many_affected` (more than 1000 components unavailable), `scenario` / `empty`.

## The simulation

`status`: `completed` (every requested analysis ran completely and every entry's impact is known),
`partial`, `unsupported` (no analysis could run: the reasons are in `runs`), or `failed` (an engine
failed and nothing else ran; `error.code` `engine_error`).

- `runs[]`: per analysis `{analysis, state (completed | partial | unsupported | failed), modelSet,
  baselineFingerprint, scenarioFingerprint, reason, message}`. Reasons: `not_concerned` (the
  scenario changes nothing that engine reads), `no_workload`, `no_pricing`, `no_currency`,
  `no_evaluator`, `engine_error`, `invalid_output`.
- `entries[]`: per entry point its failure impact `interrupted`, `degraded`, `tolerated`, `unknown`
  or `unaffected` (the Reliability Engine's semantics), with `through` (the unavailable elements it
  requires) and `missing` (what would decide an unknown impact).
- `summary` (counts only, no score): `runs` by state, `entries` by impact, `components`,
  `unavailable`, `deltas` (`comparable`, `not_comparable`, `changed`), `unsupported`.
- `overlay`: what the scenario changed, as evaluated (`changes` before → after, unavailable nodes
  and connections, zone losses, the scenario hash) — never the architecture itself.
- `inputs`: the request as stored (with the scenario snapshot), the requirements read, provider and
  currency. `trace`: how the scenario was applied and evaluated. `limitations`: always
  `model_based` and `no_defaults`; `deltas_truncated` when the output limit was reached.

## Components and deltas

`GET …/components?unavailable=&impact=&cursor=&limit=`, by node id: `{nodeId, unavailable, changes,
impact}`.

`GET …/deltas?analysis=&elementId=&comparable=&cursor=&limit=`, in canonical order (analysis,
element, metric): `{analysis, elementId (a node, a connection, or system), metric, unit, baseline,
scenario, difference, percentage, comparable, note}`. `difference` only when both sides are known;
`percentage` only when the baseline is not 0. `note` says why a value is not comparable (e.g.
`incomplete`, `unknown_line`). Values are exact decimal strings.

## Comparing simulations

`GET …/{simulationId}/comparisons/{otherSimulationId}` compares the second simulation's scenario
with the first's, per analysis, only where both ran on one common baseline (same revision, request
inputs and snapshot: the same baseline fingerprint) with the same models and engine. Otherwise the
analysis is `comparable: false` with `reason` `not_run`, `different_engine`, `different_models` or
`different_baseline`. A simulation without a result: `422 invalid_simulation_request` (`reason:
no_result`).

## Catalog

`GET /simulation/catalog`: the nine scenario types (`workload`, `replicas`, `traffic`, `resilience`,
`resources`, `component_failure`, `connection_failure`, `zone_failure`, `region_failure`) with their
inputs, properties, analyses, requirements and unsupported conditions; the evaluators in run order
(capacity, reliability, cost); and the limits (`maxChanges` 50, `maxFailures` 50,
`maxAffectedComponents` 1000, `maxDeltas` 20000).

## Audit

`architecture.simulated` (simulation id, revision, status, run, component and delta counts), in
`GET /organizations/{orgId}/audit-log`.
