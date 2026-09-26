# Reliability API

Deterministic reliability analysis of an architecture revision: request paths and their
availability, component availability, recovery time and data loss, single points of failure and
other findings, and objectives checked against modeled evidence. The same revision, request and
requirements always give the same result and `resultFingerprint`. Estimates from what the
architecture declares and stated models: **not measured, not guaranteed uptime**. What cannot be
established is unknown (`null`), never 0 or 1. See [the reliability engine](../architecture/reliability-engine.md).

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /projects/{projectId}/architectures/{architectureId}/reliability-analyses` | `architecture.analyze` | `{revision?, entries?, objectives?, assumptions?, label?}` | `201 ReliabilityAnalysis` | `404 architecture_revision_not_found`, `409 project_archived, architecture_archived`, `422 invalid_reliability_request, invalid_capacity_quantity, validation_error`, `429 rate_limited` |
| `GET /projects/{projectId}/architectures/{architectureId}/reliability-analyses` | `architecture.read` | | `200 {analyses, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/architectures/{architectureId}/reliability-analyses/{analysisId}` | `architecture.read` | | `200 ReliabilityAnalysis` | `404 reliability_analysis_not_found` |
| `GET /projects/{projectId}/architectures/{architectureId}/reliability-analyses/{analysisId}/components` | `architecture.read` | | `200 {components, nextCursor}` | `404 reliability_analysis_not_found`, `422 invalid_cursor` |
| `GET /projects/{projectId}/architectures/{architectureId}/reliability-analyses/{analysisId}/findings` | `architecture.read` | | `200 {findings, nextCursor}` | `404 reliability_analysis_not_found`, `422 invalid_cursor` |
| `GET /reliability/models` | signed in | | `200 {models}` | |

On every project endpoint: `404 project_not_found` / `architecture_not_found` outside your
organizations (existence is never revealed) and `403 permission_denied` without the permission.
Viewers read analyses; members, admins and owners also run them.

## Running an analysis

Synchronous: analyzed and stored in the request. Rate limit: 120 per user per hour. Archived
projects and architectures refuse new analyses (`409`). Component inputs are not in the request:
they are the architecture's own properties (`availability`, `replica_availability`,
`mtbf_seconds`, `mttr_seconds`, `replicas`, `min_healthy_replicas`, `failure_independence`,
`failover_mode`, `failover_seconds`, `redundancy_group`, `redundancy_group_min_healthy`,
`replication_mode`, `replication_lag_seconds`, `backup_interval_seconds`, zones and regions).

```json
{
  "entries": ["web"],
  "objectives": [
    {"key": "checkout-slo", "kind": "availability", "target": "0.999"},
    {"key": "rto", "kind": "recovery_time", "duration": {"value": 15, "unit": "min"}, "nodeIds": ["db"]},
    {"key": "rpo", "kind": "data_loss", "duration": {"value": 5, "unit": "min"}, "strict": true},
    {"key": "replicas", "kind": "redundancy", "target": 2, "nodeIds": ["api"]}
  ],
  "assumptions": [{"key": "zones", "statement": "Our zones fail independently."}]
}
```

- `entries`: where requests enter (default: every client). Unknown nodes: `422
  invalid_reliability_request` (`details: {field, reason: unknown_node, nodeId}`).
- `objectives` (at most 50, unique keys): `availability` and `redundancy` take a `target` (a fraction,
  a count), `recovery_time` and `data_loss` a `duration` with its unit; `strict` means more than /
  less than; empty `nodeIds` means the whole architecture. The project's in-force `availability` and
  `reliability` requirements with a machine-checkable constraint (availability or uptime floors,
  `rto` and `rpo` ceilings) are added as objectives with their requirement ids.
- `assumptions` are recorded with the analysis, never computed with.

## The analysis

`status`: `completed`, `partial` (some components or paths estimated), `insufficient_input`,
`unsupported`, or `failed` (`error.code` `engine_error`; nothing partial is stored).

- `paths[]`: per entry, the analyzed scope (`nodeIds`, required `connectionIds`,
  `optionalConnectionIds`) and its `availability` estimate: `quantity` (`{value, unit: "ratio"}`) or
  `null` with `missing` (e.g. `db.availability`, `eu.failure_independence`), `basis`, `inputs`
  (every value used and every assumption). There is no architecture-wide availability.
- `objectives[]`: `{key, kind, target, verdict, explanation, nodeIds, actual, missing,
  requirementId}`; `verdict` is `satisfied` or `violated` only by modeled values, `not_verifiable`
  when any is unknown (never a pass), `not_applicable` when nothing is concerned. Requirements no
  model checks (words only, durability, ranges, `user` or `region` scope) are `kind: unsupported`,
  `not_verifiable`.
- `summary`: component statuses, findings by severity, objectives by verdict, `paths`,
  `pathsEstimated`, `unsupported`. No score.
- `inputs`: the request as stored, and the requirements read (`[id, version, status]`).

## Components and findings

`GET …/components?status=&cursor=&limit=`: per node its `estimates` (`availability`,
`replica_availability`, `recovery_time`, `data_loss_window`, each with source, basis, model and
inputs), the reliability facts it declares with their provenance (`inputs`), and `missing`.

`GET …/findings?severity=&type=&certainty=&cursor=&limit=`, most severe first: `{id (stable), type,
severity, certainty (modeled | candidate), title, explanation, recommendation, nodeIds,
connectionIds, evidence, assumptions, missing, modelId, modelVersion, objective}`. Types:
`single_point_of_failure`, `no_redundancy`, `critical_dependency_without_alternative`,
`redundancy_without_failure_domain_separation`, `potential_correlated_failure`,
`inconsistent_redundancy`, `missing_failover`, `missing_recovery_data`, `unmodeled_dependency`,
`circular_dependency`, `availability_not_evaluable`, `unverified_reliability_data`,
`availability_below_objective`, `recovery_exceeds_objective`, `data_loss_exceeds_objective`,
`redundancy_below_objective`, `objective_not_evaluable`. Recommendations are options for human
review; no finding claims an outage will happen.

## Audit

`architecture.reliability_analyzed` (analysis id, revision, status, component, finding and objective
counts), in `GET /organizations/{orgId}/audit-log`.
