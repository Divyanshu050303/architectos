# Security API

Deterministic, architecture-level security analysis of an architecture revision: trust boundaries,
authentication and authorization, encryption and data protection, secrets, exposure, STRIDE threat
candidates, and the project's security policy and requirements. The same revision, request, policy
and requirements always give the same result and `resultFingerprint`. Findings come only from what
the architecture declares: a control that is not modeled is reported as **not modeled**, never taken
as present or absent. The analysis **does not prove the absence of vulnerabilities** and does not
replace secure implementation review, penetration testing, dependency scanning or operational
controls; findings require engineering review. No score, and no compliance claim.

This is about the analyzed system. ArchitectOS's own sign-in, sessions and roles are the platform's
([authentication](../security/authentication.md)).

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /projects/{projectId}/architectures/{architectureId}/security-analyses` | `architecture.analyze` | `{revision?, scope?, analyzers?, assumptions?, label?}` | `201 SecurityAnalysis` | `404 architecture_revision_not_found`, `409 project_archived, architecture_archived`, `422 invalid_security_request, validation_error`, `429 rate_limited` |
| `GET /projects/{projectId}/architectures/{architectureId}/security-analyses` | `architecture.read` | | `200 {analyses, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/architectures/{architectureId}/security-analyses/{analysisId}` | `architecture.read` | | `200 SecurityAnalysis` | `404 security_analysis_not_found` |
| `GET /projects/{projectId}/architectures/{architectureId}/security-analyses/{analysisId}/components` | `architecture.read` | | `200 {components, nextCursor}` | `404 security_analysis_not_found`, `422 invalid_cursor` |
| `GET /projects/{projectId}/architectures/{architectureId}/security-analyses/{analysisId}/findings` | `architecture.read` | | `200 {findings, nextCursor}` | `404 security_analysis_not_found`, `422 invalid_cursor` |
| `GET /security/analyzers` | signed in | | `200 {analyzers}` | |

On every project endpoint: `404 project_not_found` / `architecture_not_found` outside your
organizations (existence is never revealed) and `403 permission_denied` without the permission.
Viewers read analyses; members, admins and owners also run them.

## Running an analysis

Synchronous: analyzed and stored in the request. Rate limit: 120 per user per hour. Archived
projects and architectures refuse new analyses (`409`). Security inputs are not in the request: they
are the architecture's own properties (`exposure`, `authentication`, `authorization`,
`sensitive_operations`, `management_interface`, `data_classification`, `personal_data`,
`encryption_at_rest`, `secrets_required`, `secret_source`, `secret_rotation`, `audit_logging`; on
connections `tls`, `authentication`, `data_classification`, `personal_data`; trust zones and their
`trust_level`), and the policy and requirements are the project's (the
[architecture policy](projects.md) and in-force requirements of type `security`).

```json
{
  "scope": ["api", "db"],
  "analyzers": ["trust-boundaries", "authentication", "threat-model"],
  "assumptions": [{"key": "waf", "statement": "A WAF filters public traffic."}],
  "label": "Before launch"
}
```

- `scope`: components to analyze (with the connections touching them); default all. Unknown nodes,
  clients and boundaries: `422 invalid_security_request` (`details: {field: "scope", reason:
  unknown_node, nodeId}`).
- `analyzers`: ids from `GET /security/analyzers`; default all. Unknown: `reason: unknown_analyzer`;
  one without an analyzer it builds on: `reason: requires_analyzer`. The threat model selected with
  none of its sources reports `no_source_findings` rather than an empty result.
- `assumptions` are recorded with the analysis, never computed with.

## The analysis

`status`: `completed` (every component models what matters for it and every analyzer ran),
`partial`, `insufficient_input` (no component models any security property: unknown is not
secure), `unsupported` (nothing in scope), or `failed` (`error.code` `engine_error`; nothing partial
is stored).

- `trustZones[]`: `{boundaryId, trustLevel (null: not modeled), nodeIds}`.
- `checks[]`: one per active policy rule and per mapped requirement condition: `{key (policy.<rule>
  or requirement.<ref>[.<condition>]), source, condition, verdict, explanation, nodeIds,
  connectionIds, actual, missing, requirementId, policyRule, mapping}`. `verdict` is `satisfied` or
  `violated` only by modeled evidence, `not_verifiable` when anything deciding it is not declared or
  the requirement's words match no supported condition (`condition: unsupported`) — never a pass —
  and `not_applicable` when nothing is concerned. `mapping` records how a requirement's words became
  its condition, e.g. `encryption + 'at rest' (sensitive only)`.
- `summary`: components by coverage, findings by severity, basis and category, threat candidates by
  STRIDE category, checks by verdict. No score.
- `inputs`: the request as stored, the project's `policy` as it was, and the requirements read
  (`[id, version, status]`).
- `limitations`: always `architecture_level` and `no_defaults`.

## Components and findings

`GET …/components?coverage=&cursor=&limit=`: per component its `coverage` (`modeled`, `partial`,
`not_modeled`), declared `exposure` and `sensitive` (`null` when not established), `trustZoneIds`,
the security facts it declares with their provenance (`inputs`) and what it should declare but does
not (`missing`).

`GET …/findings?severity=&type=&category=&basis=&certainty=&threat=&cursor=&limit=`, most severe
first: `{id (stable, sec_…), type, category, basis, severity, certainty (modeled | candidate), title,
explanation, recommendation, nodeIds, connectionIds, boundaryIds, evidence, assumptions, missing,
analyzerId, analyzerVersion, threat, requirementId, policyRule, checkKey (the check a requirement or
policy finding reports)}`. `basis` keeps the kinds apart:
`control_gap` (a control declared absent where it matters), `potential_risk` (the model could allow
harm), `violation` (a requirement or policy contradicted by modeled evidence), `not_evaluable` (not
modeled enough to decide). Types: `unprotected_boundary_crossing`, `crossing_controls_not_modeled`,
`sensitive_data_crosses_boundary`, `trust_level_not_modeled`, `inconsistent_trust_boundary`,
`insufficient_flow_semantics`, `missing_authentication`, `unauthenticated_connection`,
`inconsistent_authentication`, `authentication_not_modeled`, `missing_authorization`,
`authorization_not_modeled`, `unencrypted_data_at_rest`, `unencrypted_data_in_transit`,
`encryption_not_modeled`, `data_classification_not_modeled`, `hardcoded_secret`,
`secret_in_configuration`, `secret_source_not_modeled`, `public_management_interface`,
`sensitive_component_reachable_from_public`, `exposure_not_modeled`, `threat_candidate` (with its
STRIDE `threat`), `requirement_violated`, `requirement_not_evaluable`, `policy_violated`,
`policy_not_evaluable`. Recommendations are for engineering review; nothing is changed
automatically.

**No secret is returned or stored.** A setting in the architecture whose name looks like a secret is
reported by its path, its value `"[redacted]"`; engine failures are logged by the error's type only.

## Audit

`architecture.security_analyzed` (analysis id, revision, status, component, finding and check counts),
in `GET /organizations/{orgId}/audit-log`.
