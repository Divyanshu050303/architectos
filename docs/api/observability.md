# Observability API

Deterministic, architecture-level observability analysis of an architecture revision: logging,
metrics, tracing and trace-context propagation, health checks, alerting paths, telemetry collection,
and the project's observability policy and SLO / monitoring requirements. The same revision,
request, policy and requirements always give the same result and `resultFingerprint`.

The engine **analyzes architecture-level configuration only**. It does not collect or query live
telemetry, does not prove that instrumentation works, and does not calculate SLO attainment (no
runtime measurements are read). What the architecture does not declare is **unknown**: never taken
as configured, never as absent. Findings require engineering review. No score, no percentage.

This is about the analyzed system, not about ArchitectOS's own logs and metrics.

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /projects/{projectId}/architectures/{architectureId}/observability-analyses` | `architecture.analyze` | `{revision?, scope?, analyzers?, requirementIds?, assumptions?, label?}` | `201 ObservabilityAnalysis` | `404 architecture_revision_not_found`, `409 project_archived, architecture_archived`, `422 invalid_observability_request, validation_error`, `429 rate_limited` |
| `GET /projects/{projectId}/architectures/{architectureId}/observability-analyses` | `architecture.read` | | `200 {analyses, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/architectures/{architectureId}/observability-analyses/{analysisId}` | `architecture.read` | | `200 ObservabilityAnalysis` | `404 observability_analysis_not_found` |
| `GET /projects/{projectId}/architectures/{architectureId}/observability-analyses/{analysisId}/components` | `architecture.read` | | `200 {components, nextCursor}` | `404 observability_analysis_not_found`, `422 invalid_cursor, invalid_observability_request` |
| `GET /projects/{projectId}/architectures/{architectureId}/observability-analyses/{analysisId}/findings` | `architecture.read` | | `200 {findings, nextCursor}` | `404 observability_analysis_not_found`, `422 invalid_cursor` |
| `GET /observability/analyzers` | signed in | | `200 {analyzers}` | |

On every project endpoint: `404 project_not_found` / `architecture_not_found` outside your
organizations (existence is never revealed) and `403 permission_denied` without the permission.
Viewers read analyses; members, admins and owners also run them.

## Running an analysis

Synchronous: analyzed and stored in the request. Rate limit: 120 per user per hour. Archived
projects and architectures refuse new analyses (`409`). Observability inputs are not in the request —
and no telemetry is accepted: they are the architecture's own properties (on components
`criticality`, `logs`, `structured_logs`, `correlation_ids`, `metrics`, `traces`, `trace_context`,
`trace_sampling_ratio`, `health_check`, `alerts`, `owner`; on observability components
`alert_delivery` and `retention_seconds`; on connections `telemetry`, `trace_propagation`,
`health_check`), and the policy and requirements are the project's (the
[architecture policy](projects.md) and in-force requirements).

```json
{
  "scope": ["api", "orders"],
  "analyzers": ["logs", "traces", "requirements"],
  "requirementIds": ["01a0de60-a784-7244-8ecc-62afb5352f47"],
  "assumptions": [{"key": "collector-ha", "statement": "The collector runs in two zones."}],
  "label": "Before launch"
}
```

- `scope`: components to analyze (with the connections touching them); default all. Unknown nodes,
  clients and boundaries: `422 invalid_observability_request` (`details: {field: "scope", reason:
  unknown_node, nodeId}`).
- `analyzers`: ids from `GET /observability/analyzers`; default all. Unknown: `reason:
  unknown_analyzer`.
- `requirementIds`: in-force requirements to evaluate; default all in force. Not in force in this
  project: `reason: unknown_requirement`; an empty list: `reason: empty`.
- `assumptions` are recorded with the analysis, never computed with.

## The analysis

`status`: `completed` (every applicable dimension of every component is declared and every analyzer
ran), `partial`, `insufficient_input` (components exist, none declares any observability: unknown is
not configured), `unsupported` (nothing in scope), or `failed` (`error.code` `engine_error`; nothing
partial is stored).

- `summary` (counts only, reproducible from the components, findings and checks): `scope`
  (`components`, `eligible`, `unsupported` — third parties, counted apart), `criticality`,
  `coverage` and `criticalCoverage` (per dimension, components by state), `collection` (per signal:
  `declared`, `collected`, `notCollected`), `objectives` (`measurable` and `alerted` checks by
  verdict, `unsupportedRequirements`), `findings` by severity, `bases`, `categories`, `checks` by
  verdict, and `priorities` (the first finding ids in priority order).
- `checks[]`: one per active policy rule and per requirement condition: `{key (policy.<rule> or
  requirement.<ref>[.<condition>]), source, condition, verdict, explanation, nodeIds, connectionIds,
  actual, missing, requirementId, policyRule, mapping}`. Conditions: `logs_on_critical`,
  `metrics_on_critical`, `traces_on_critical`, `propagation_on_critical`,
  `health_checks_on_critical`, `alerting_on_critical`, `structured_logs`, `correlation_ids`,
  `ownership`, `telemetry_retention`, `telemetry_collected`, `objective_measurable`,
  `objective_alerted`, `unsupported`. `verdict` is `satisfied` or `violated` only by modeled
  evidence, `not_verifiable` when anything deciding it is not declared or the requirement maps to no
  supported condition — never a pass — and `not_applicable` when nothing is concerned. An objective
  check that is satisfied means a measurable (or alerted) indicator is modeled, **never that the
  objective is met**.
- `inputs`: the request as stored, the project's `policy` as it was, and the requirements read
  (`[id, version, status]`).
- `limitations`: always `configuration_only` and `no_defaults`.

## Components and findings

`GET …/components?criticality=&dimension=&state=&cursor=&limit=`: per component its declared
`criticality` (`null`: not modeled), its `coverage` per dimension (`logging`, `metrics`, `tracing`,
`healthChecks`, `alerting` → `modeled`, `partial`, `absent`, `unknown`, `unsupported`), the facts it
declares with their provenance (`inputs`) and what it does not declare (`missing`). `criticality`
filters by `critical`, `standard` or `not_modeled`; `state` filters one `dimension` (both are needed:
`state` alone is `422 invalid_observability_request`).

`GET …/findings?severity=&type=&category=&basis=&certainty=&dimension=&cursor=&limit=`, in priority
order (severity, then basis: violation, control gap, potential risk, not evaluable): `{id (stable,
obs_…), type, category, basis, severity, certainty (modeled | candidate), title, explanation,
recommendation, nodeIds, connectionIds, evidence, assumptions, missing, analyzerId, analyzerVersion,
dimension, requirementId, policyRule, checkKey}`. Types: `criticality_not_modeled`, `logs_absent`,
`logs_not_modeled`, `sensitive_data_in_logs`, `metrics_absent`, `metrics_not_modeled`,
`traces_absent`, `traces_not_modeled`, `propagation_broken`, `propagation_not_modeled`,
`telemetry_not_collected`, `health_check_absent`, `health_check_not_modeled`,
`health_check_unconsumed`, `alerts_absent`, `alerts_not_modeled`, `alert_without_signal`,
`alert_delivery_not_modeled`, `requirement_violated`, `requirement_not_evaluable`,
`policy_violated`, `policy_not_evaluable`. Recommendations are for engineering review; nothing is
changed automatically.

**No secret is returned or stored.** A setting whose name looks like a secret is reported by its path,
its value `"[redacted]"`; engine failures are logged by the error's type only.

## Audit

`architecture.observability_analyzed` (analysis id, revision, status, component, finding and check
counts), in `GET /organizations/{orgId}/audit-log`.
