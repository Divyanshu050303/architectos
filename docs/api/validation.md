# Validation API

Deterministic validation of an architecture revision: the same revision, rule set, requirements,
policy and configuration always give the same findings, verdicts and `resultFingerprint`. No
language model takes part. See [the architecture policy](projects.md) and
[the IR](../architecture/architecture-ir.md).

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /projects/{projectId}/architectures/{architectureId}/validations` | `architecture.validate` | `{revision?, profile?, rules?, parameters?, severityOverrides?}` | `201 ValidationRun` | `404 architecture_revision_not_found`, `409 project_archived, architecture_archived`, `422 invalid_validation_config, validation_error`, `429 rate_limited` |
| `GET /projects/{projectId}/architectures/{architectureId}/validations` | `architecture.read` | | `200 {runs, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/architectures/{architectureId}/validations/{runId}` | `architecture.read` | | `200 ValidationRun` | `404 validation_run_not_found` |
| `GET /projects/{projectId}/architectures/{architectureId}/validations/{runId}/findings` | `architecture.read` | | `200 {findings, nextCursor}` | `404 validation_run_not_found`, `422 invalid_cursor` |
| `GET /validation/rules` | signed in | | `200 {rules, profiles}` | |

On every project endpoint: `404 project_not_found` / `architecture_not_found` when the project or
architecture does not exist, was deleted, or is not in one of your organizations (existence is
never revealed), and `403 permission_denied` without the permission. Viewers read runs; members,
admins and owners also run validations.

## Running a validation

Synchronous: the run is validated and stored in the request, and returned `completed` or `failed`.

- **completed**: the engine produced a result. Findings are a successful validation.
- **failed**: the engine could not produce a trustworthy result (`error.code`: `engine_error`,
  `too_many_requirements`); nothing partial is stored.

(`pending` and `running` are reserved for a future background worker.)

The request chooses a `profile` (`default`, `strict`), optionally a subset of `rules` (mandatory
rules always run), rule `parameters` (by rule id, then name) and `severityOverrides` (by rule id;
not for mandatory rules). What the engine does not offer is refused before anything runs:
`422 invalid_validation_config` with `details.reason` (`unknown_profile`, `unknown_rule`,
`too_many_rules`, `rule_not_selected`, `unknown_parameter`, `not_an_integer`, `not_a_boolean`,
`not_text`, `not_a_choice`, `out_of_range`, `mandatory_rule`, `invalid_severity`), `details.ruleId`
and `details.parameter`.

The engine reads the revision, the project's live requirements and its architecture policy, and the
run records what it was given (`inputs`: configuration, policy, number of requirements), so a result
stays explainable after they change. Validating is a write: archived projects and architectures
refuse it (`409`). Rate limit: 120 runs per user per hour.

## ValidationRun

```json
{
  "id": "0199...", "projectId": "0199...", "architectureId": "0199...",
  "revision": 3, "revisionContentHash": "9f2c...", "profile": "default",
  "status": "completed", "requestedByUserId": "0199...",
  "requestedAt": "…", "startedAt": "…", "completedAt": "…",
  "resultFingerprint": "5d1e...", "contextFingerprint": "a0c4...",
  "ruleSet": {"id": "default", "version": "4b9a0c1d2e3f4a5b", "rules": [["structure.synchronous-cycle", 1]]},
  "summary": {
    "total": 4, "blocking": 1, "ruleFailures": 0,
    "bySeverity": {"critical": 0, "high": 2, "medium": 1, "low": 1, "info": 0},
    "byCategory": {"structure": 1, "configuration": 1, "completeness": 0, "policy": 1, "requirements": 1},
    "requirements": {"satisfied": 2, "violated": 1, "not_verifiable": 3, "not_applicable": 0}
  },
  "requirementResults": [
    {"requirementId": "0199...", "reference": "REQ-4", "requirementVersion": 2, "verdict": "violated",
     "reason": "1 element(s) are outside the required regions (eu-west-1).", "ruleId": "requirements.verdicts",
     "entityIds": ["db"], "evidence": [{"label": "required", "value": "eu-west-1"}]}
  ],
  "failures": [],
  "limitations": [{"code": "catalog_unavailable", "message": "…"}],
  "inputs": {"config": {"profile": "default", "rules": null, "parameters": {}, "severity_overrides": {}},
             "policy": {"require_tls": true, "…": "…"}, "requirementCount": 7},
  "error": null
}
```

Counts only: no score is derived from them. A requirement verdict is `satisfied`, `violated`,
`not_verifiable` (never a pass; the reason says why) or `not_applicable`. `failures` lists rules
that could not execute (`unexpected_error`, `invalid_output`): their absence of findings proves
nothing. `limitations` states what no rule of the run could check (`catalog_unavailable`,
`no_policy`, `requirements_not_provided`).

## Findings

`GET …/findings?severity=&category=&blocking=&ruleId=&entityId=&cursor=&limit=` (at most 500 per
page), most severe first:

```json
{"id": "fnd_3f0a9c2b7d1e4f6a8b0c", "ruleId": "policy.tls", "ruleVersion": 1, "code": "tls_disabled",
 "severity": "high", "category": "policy", "title": "…", "explanation": "…", "remediation": "…",
 "entityIds": ["api-db"], "fieldPaths": ["configuration.tls"], "expected": "true", "actual": "false",
 "evidence": [], "blocking": true, "requirementId": null, "policyRule": "require_tls"}
```

A finding's `id` is stable for the same finding on the same content, across runs.

## Rules

`GET /validation/rules` lists every rule: `id`, `version`, `name`, `description`, `category`,
default `severity`, `profiles`, `inputs` (`requirements`, `policy`), `mandatory` and `parameters`.

## Audit

`architecture.validated` (run id, revision, profile, status, finding, blocking and rule-failure
counts; never findings or content), in `GET /organizations/{orgId}/audit-log`.
