# Capacity API

Deterministic capacity analysis of an architecture revision under an explicit workload. The same
revision, workload, models, parameters, assumptions and scenarios always give the same result and
`resultFingerprint`. No language model takes part, nothing is measured, and every number says where
it comes from. See [the capacity engine](../architecture/capacity-engine.md).

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /projects/{projectId}/architectures/{architectureId}/capacity-analyses` | `architecture.analyze` | `{revision?, workload, models?, parameters?, assumptions?, entries?, label?, scenarios?}` | `201 CapacityAnalysis` | `404 architecture_revision_not_found`, `409 project_archived, architecture_archived`, `422 invalid_workload_profile, invalid_capacity_quantity, invalid_capacity_config, invalid_capacity_scenario, validation_error`, `429 rate_limited` |
| `GET /projects/{projectId}/architectures/{architectureId}/capacity-analyses` | `architecture.read` | | `200 {analyses, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/architectures/{architectureId}/capacity-analyses/{analysisId}` | `architecture.read` | | `200 CapacityAnalysis` | `404 capacity_analysis_not_found` |
| `GET /projects/{projectId}/architectures/{architectureId}/capacity-analyses/{analysisId}/components` | `architecture.read` | | `200 {components, nextCursor}` | `404 capacity_analysis_not_found`, `422 invalid_cursor` |
| `GET /projects/{projectId}/architectures/{architectureId}/capacity-analyses/{analysisId}/bottlenecks` | `architecture.read` | | `200 {bottlenecks}` | `404 capacity_analysis_not_found` |
| `GET /projects/{projectId}/architectures/{architectureId}/capacity-analyses/{analysisId}/scenarios` | `architecture.read` | | `200 {scenarios}` | `404 capacity_analysis_not_found` |
| `GET /capacity/models` | signed in | | `200 {models}` | |

On every project endpoint: `404 project_not_found` / `architecture_not_found` outside your
organizations (existence is never revealed) and `403 permission_denied` without the permission.
Viewers read analyses; members, admins and owners also run them.

## Running an analysis

Synchronous: analyzed and stored in the request. The analysis `status` is what the result
established: `completed` (every component in scope has estimates), `partial`, `insufficient_input`
(models apply but inputs are missing), `unsupported` (no model applies), or `failed` (the engine
could not run: `error.code` `engine_error`; nothing partial is stored). Rate limit: 120 per user per
hour. Archived projects and architectures refuse new analyses (`409`).

```json
{
  "workload": {
    "name": "Checkout peak", "type": "request_response",
    "peakRate": {"value": 2000, "unit": "requests/second"},
    "readRatio": "0.8",
    "requestPayload": {"value": 2, "unit": "KB"}, "responsePayload": {"value": 10, "unit": "KB"},
    "targetUtilization": "0.7",
    "assumptions": [{"key": "peak_factor", "statement": "Peak is 3x the daily average."}],
    "requirementIds": ["0199..."]
  },
  "entries": ["web"],
  "scenarios": [
    {"name": "Double", "growth": 2},
    {"name": "Scale out", "growth": 2, "changes": [{"elementId": "api", "property": "replicas", "value": 7}]}
  ]
}
```

Workload types: `request_response` (requires `peakRate` in a request rate), `event_stream`
(`peakRate` in an event rate), `batch` (`batchSize` and `batchInterval`). Units are explicit and
case-sensitive: `requests/second|minute|hour|day`, `events/second|minute|hour`,
`operations/second|minute`, `B/s`, `KB/s`, `MB/s`, `GB/s`, `B`, `KB`, `MB`, `GB`, `TB`, `ms`, `s`,
`min`, `h`, `d`, `connections`, `users`, `cores`, `millicores`, `replicas`, `ratio`, `%`. Values are
non-negative, finite, at most 9 decimal places, below 10^15. Cited `requirementIds` must be live
requirements of the project (`invalid_workload_profile`, `reason: unknown_requirement`).

Scenarios (at most 10, unique names): one of `growth` (multiplier), `growthRate` with `periods`
(compounded), `targetRate`; optional `targetUtilization`; `changes` to capacity and traffic
properties of named nodes and connections. `invalid_capacity_scenario` says which field and why.
Model selection (`models`, `parameters`) is refused with `invalid_capacity_config` (`reason`:
`unknown_model`, `model_not_selected`, `unknown_parameter`, …); an unknown entry node too
(`unknown_entry`).

## CapacityAnalysis

`id`, `revision`, `revisionContentHash`, `label`, `status`, `requestedByUserId`, timestamps,
`resultFingerprint`, `contextFingerprint`, `modelSet {version, models}`, `inputs` (the request as
stored, snake_case), `summary`, `connections` (demand per connection, with path and factors),
`unsupported` (what could not be calculated and why), `limitations` (`catalog_unavailable`,
`no_measurements`, …), `scaling` and `unsupportedScaling` (for the baseline), `scenarios`, `error`.

`summary`: counts of components per status and bottlenecks per certainty, `unsupported`,
`highestUtilization`, `saturationMultiple` (the workload multiple at which the first known limit is
reached) and `saturationComplete` (true only when every component the workload reaches has a known
throughput capacity). No score.

## Components

`GET …/components?status=&cursor=&limit=` (at most 500): per node, `status`, the `models` that
applied, `demand` (each with `path` and `factors`), `limits` and `resources` (each an estimate:
`quantity` or `null` when unknown, `source` — `declared`, `model_estimate`, `assumed`, `unknown` —
`basis`, `inputs`, `missing`), `utilization` (per resource: `demand`, `capacity`, `state`, `ratio`,
`headroom`, `relativeHeadroom`, `headroomToTarget`), `missing` and `notes`.

## Bottlenecks and scenarios

`GET …/bottlenecks?certainty=modeled|candidate`: `condition` (`exceeds_capacity`, `at_capacity`,
`above_target`, `no_capacity`, `unknown_capacity`), `certainty`, `utilization`, `explanation`,
`remediation`, `evidence`, `assumptions`. `GET …/scenarios`: per scenario its `status`, `summary`,
`bottlenecks`, `scaling`, `unsupportedScaling` and `comparison` (changed inputs and configuration,
per-resource before/after, new and resolved bottlenecks).

## Models

`GET /capacity/models`: `id`, `version`, `name`, `description`, `kinds`, `resources`,
`configuration`, `workload` and `assumptions` it requires, `parameters`, `limitations`.

## Audit

`architecture.capacity_analyzed` (analysis id, revision, status, component, bottleneck, unsupported
and scenario counts; never workloads, names or results), in `GET /organizations/{orgId}/audit-log`.
