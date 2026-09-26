# Requirements API

Conventions: see [authentication.md](authentication.md). Every endpoint requires a Bearer token and
is reached **through its project**: `/projects/{projectId}/...`. Domain rules, taxonomy and units:
[docs/domain/requirements.md](../domain/requirements.md).

On every endpoint: `404 project_not_found` when the project does not exist, was deleted, or you are
not a member of its organization; `404 requirement_not_found` for a requirement that is deleted or
belongs to another project; `403 permission_denied` when your role lacks the permission. Writes on an
archived project answer `409 project_archived`.

Roles: viewers read everything (including history, analysis, sets and planning input); members also
create, change and delete requirements and create requirement sets.

## Requirements

| Method and path | Permission | Success | Errors |
|---|---|---|---|
| `GET /projects/{id}/requirements` | `requirement.read` | `200 {requirements, nextCursor}` | `422 invalid_cursor, validation_error` |
| `POST /projects/{id}/requirements` | `requirement.create` | `201 Requirement` | `422 invalid_requirement, validation_error`, `429 rate_limited` |
| `GET /projects/{id}/requirements/{rid}` | `requirement.read` | `200 Requirement` | |
| `PATCH /projects/{id}/requirements/{rid}` | `requirement.update` | `200 Requirement` | `409 requirement_version_conflict, invalid_status_transition, requirement_locked`, `422 invalid_requirement, change_reason_required, nothing_to_update` |
| `DELETE /projects/{id}/requirements/{rid}` | `requirement.delete` | `204` (soft delete; history kept) | |

### Requirement

```json
{
  "id": "0199...", "projectId": "0199...", "reference": "REQ-12", "number": 12, "version": 2,
  "type": "capacity", "category": "throughput", "scope": "api", "title": "API throughput",
  "statement": "The API must support 5,000 requests per second.",
  "priority": "critical", "status": "active", "source": "user", "confidence": null,
  "structuredData": {"metric": "requests_per_second", "operator": ">=", "value": "5000", "unit": "requests/second"},
  "normalizedData": {"metric": "requests_per_second", "operator": ">=", "value": "5000", "unit": "requests/second"},
  "createdByUserId": "0199...", "createdAt": "...", "updatedAt": "..."
}
```

- `reference` (`REQ-12`) is unique in the project and never reused, even after deletion.
- `version` is the current version: send it back as `expectedVersion` when changing the requirement.
- `structuredData` is what was stored (explicit unit); `normalizedData` is derived, in the canonical
  unit, and is what the engines consume. Numbers are exact decimal **strings**.
- `scope`: `system` (also "unspecified"), `service`, `api`, `database`, `queue`, `user`, `region`,
  `data`; defaults to `system`.
- `confidence` is confidence in the interpretation, not in the requirement being true, and unrelated
  to priority: required for `source` `ai` and `discovery`, optional for `system` and `imported`,
  refused for `user`.
- Enumerations are lower-case: `type` (`functional`, `non_functional`, `capacity`, `performance`,
  `availability`, `reliability`, `security`, `data`, `compliance`, `operational`, `cost`), `priority`
  (`critical`, `high`, `medium`, `low`), `status` (`draft`, `active`, `satisfied`, `invalid`,
  `deprecated`), `source` (`user`, `ai`, `system`, `imported`, `discovery`).

### Create

```json
{"type": "capacity", "category": "throughput", "title": "API throughput",
 "statement": "Handle 2k requests/sec at peak.", "priority": "critical", "status": "active",
 "structuredData": {"metric": "rps", "operator": ">=", "quantity": "2k requests/sec"}}
```

`status` is `draft` (default) or `active`; only `source: "user"` (the default) may start `active`.
Constraints use `>=`, `>`, `<=`, `<`, `==` (a target) with `value`, or `"between"` with `min` and
`max` (inclusive). Convenient input is normalized
deterministically (see [normalization](../domain/requirements.md#normalization)); ambiguous spellings
such as `5m` or `gb` are refused.

### Change

```json
{"expectedVersion": 1, "structuredData": {"metric": "requests_per_second", "operator": ">=",
 "value": "5000", "unit": "requests/second"}, "changeReason": "Traffic forecast increased from 2K to 5K RPS"}
```

Any of `category`, `scope`, `title`, `statement`, `priority`, `status`, `structuredData` (`{}` removes
the constraint). Type, source, confidence and project never change. Every change creates a new immutable
version. `changeReason` is required when the requirement is active or satisfied. A change that leaves
everything as it is returns the requirement unchanged (no version).

- `409 requirement_version_conflict` with `details.currentVersion`: someone changed it since you
  loaded it; reload and reapply.
- `409 invalid_status_transition` with `details.from`, `details.to`.
- `409 requirement_locked` with `details.status`: satisfied requirements must be reopened
  (`status: "active"`, possibly in the same request); deprecated ones never change.
- `422 invalid_requirement` with `details.field` (camelCase path, e.g. `structuredData.value`) and
  `details.reason` (e.g. `out_of_range`, `unknown_unit`, `ambiguous_unit`, `unknown_for_type`,
  `required_when_in_force`, `unsatisfiable`).

### Listing

`GET /projects/{id}/requirements?type=&category=&status=&priority=&search=&cursor=&limit=`: filters
combine; `search` (1–100 characters) matches title or statement case-insensitively and literally;
newest first; `limit` 1–100 (default 50).

## Validation and analysis (read-only, not audited)

| Method and path | Success |
|---|---|
| `POST /projects/{id}/requirements/{rid}/validate` | `200 {requirement: {id, reference, version}, valid, readyForActive, issues: [{severity, field, reason}]}` |
| `GET /projects/{id}/requirements/analysis` | `200 Analysis` |

`validate` checks the current version against **today's** rules and whether it could become active,
plus warnings (`missing_constraint`, `missing_percentile`, `low_confidence`, `not_ready_for_active`).
Severities are `blocking`, `warning` and `info`.

`analysis` covers draft, active and satisfied requirements; every finding names the exact
`{id, reference, version}` it was computed from:

```json
{
  "requirements": [{"id": "...", "reference": "REQ-1", "version": 1}, ...],
  "truncated": false,
  "conflicts": [{"reason": "disjoint_bounds", "metric": "requests_per_second",
                 "requirements": [{...REQ-1...}, {...REQ-2...}],
                 "message": "REQ-1 requires requests_per_second >= 10000 requests/second, but REQ-2 requires requests_per_second <= 5000 requests/second: no value satisfies both."}],
  "completeness": {"covered": [{"concern": "traffic", "requirements": [...]}],
                   "missing": ["availability", "data", "security", "retention"]},
  "ambiguous": [{"requirement": {...}, "reason": "missing_percentile"}],
  "unbounded": [{"metric": "storage", "reason": "no_lower_bound", "requirements": [...]}]
}
```

## History (read-only)

| Method and path | Success |
|---|---|
| `GET /projects/{id}/requirements/{rid}/versions?cursor=&limit=` | `200 {versions, nextCursor}`, oldest first |
| `GET /projects/{id}/requirements/{rid}/versions/{version}` | `200 RequirementVersion` or `404 requirement_version_not_found` |

A version is the full state at that point (`type` … `structuredData`, `normalizedData`) plus
`version`, `changeReason`, `createdByUserId` (who made the change) and `createdAt`. There is no way to
change or delete a version (`405`). The history of a deleted requirement is not served (it is kept for
the requirement sets that pinned it).

## Requirement sets

| Method and path | Permission | Success | Errors |
|---|---|---|---|
| `POST /projects/{id}/requirement-sets` | `requirement_set.create` | `201 RequirementSet` | `409 project_archived, requirement_set_conflicts`, `422 invalid_requirement_set`, `429 rate_limited` |
| `GET /projects/{id}/requirement-sets?cursor=&limit=` | `requirement.read` | `200 {requirementSets, nextCursor}` (newest first, without `requirements`) | `422 invalid_cursor` |
| `GET /projects/{id}/requirement-sets/{setId}` | `requirement.read` | `200 RequirementSet` | `404 requirement_set_not_found` |
| `GET /projects/{id}/requirement-sets/{setId}/planning-input` | `requirement.read` | `200 {requirementSetId, schemaVersion, contentHash, planningInput}` | `404 requirement_set_not_found` |

Body: `{name?, description?, requirementIds?}`. Without `requirementIds`, every active or satisfied
requirement is pinned at its current version. Refusals (`422 invalid_requirement_set`):
`details.reason` `nothing_in_force`, `empty`, `too_many` (over 1000), or per requirement
(`details.requirementId`) `not_found`, `not_in_force`, `invalid`, `duplicate`.
`409 requirement_set_conflicts` lists the contradicting requirements in `details.conflicts`.

```json
{"id": "...", "projectId": "...", "number": 3, "label": "v3", "name": "Launch baseline", "description": "",
 "schemaVersion": 1, "contentHash": "9f2c...", "requirementCount": 2, "createdByUserId": "...", "createdAt": "...",
 "requirements": [{"requirementId": "...", "reference": "REQ-1", "version": 2}, ...]}
```

Sets never change. `planningInput` is the [Architecture Planning Input](../domain/requirements.md#the-architecture-boundary)
exactly as stored at creation (snake_case keys: it is a versioned engine contract).
`contentHash` = SHA-256 of `json.dumps(planningInput, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
in UTF-8: anyone can verify it, and equal content gives an equal hash.

## Requirement analyses (Requirements Engine)

How the engine works, and why: [docs/requirements-engine.md](../requirements-engine.md).

| Method and path | Permission | Success | Errors |
|---|---|---|---|
| `POST /projects/{id}/requirement-analyses` `{input}` | `requirement.create` | `201 Analysis` | `409 project_archived`, `413 payload_too_large`, `422 invalid_requirement_input`, `429 rate_limited` |
| `GET /projects/{id}/requirement-analyses/{analysisId}` | `requirement.read` | `200 Analysis` | `404 requirement_analysis_not_found` |
| `POST /projects/{id}/requirement-analyses/{analysisId}/promote` `{candidateKeys}` | `requirement.create` | `200 {promotions: [{candidateKey, created, requirement}]}` | `409 project_archived`, `422 invalid_promotion` |

**Analyze** runs the Requirements Engine over a plain-language description (at most 20,000
characters; rate-limited to 60 per user per hour) and stores the analysis: the text exactly as
written, the engine version and the result. **Nothing becomes a requirement.** The analysis lists:

- `candidates`: proposed requirements, each with a deterministic `key`, the exact `span` of the
  input it came from, `method` (`pattern` or `llm`), `source` (`system` or `ai`), `confidence`
  (in the interpretation), its classification, `structuredData` and `normalizedData`;
- findings, each `{key, kind, code, severity, message, suggestion, span, candidateKeys,
  requirementReferences, options, ...}`, grouped as `issues` (invalid, rejected, duplicate,
  unresolved, extraction), `ambiguities`, `assumptions`, `conflicts` (contradictions and
  consistency notes, also against the project's existing requirements) and `completeness.findings`;
- `completeness`: `status` (complete, incomplete, unknown), the detected `profiles` with their
  evidence, each area's `importance`, and the `covered` and `missing` areas;
- `readyForArchitecture`: true when no finding is `blocking` (`blocking` lists their keys);
- `semantic`: whether a language model was consulted, why, with which prompt version, its status
  and token usage (the model is only asked when the rules leave text unread, and its proposals are
  validated like everything else).

**Promote** turns chosen candidates into **draft** requirements (at most 100 per call), validated
like any creation. Idempotent: a candidate promoted before returns its requirement with
`created: false`.

Requirements promoted from an analysis keep their provenance. A
promoted requirement is always a draft and keeps its origin: the analysis, which stores the raw
input exactly as written, and the candidate, which points at the exact span of that text. The
planning input carries it as `origin: {analysis_id, candidate_key}`.

- `422 invalid_requirement_input`: the text to analyze is empty, longer than 20,000 characters, or
  contains control characters (`details.reason`).
- `404 requirement_analysis_not_found`: no such analysis in this project.
- `409 candidate_already_promoted`: that candidate is already a live requirement
  (`details.requirementId`); promoting twice never creates a duplicate.
- `422 invalid_promotion`: no candidates chosen, more than 100, one chosen twice, or one that is
  not in the analysis (`details.reason`, `details.candidateKey`).

Promotion is recorded as `requirement.promoted` (the reference, the analysis and the candidate);
an analysis as `requirement_analysis.created` (engine version and counts, never the text).

**Limits.** An analysis considers at most 100 candidates and reports at most 300 findings; beyond
that an `extraction` warning (`too_many_candidates`, `findings_truncated`) says what was left out.
Blocking findings are never the ones cut. A repeated vague phrase ("fast", "fast", ...) is one
finding at its first occurrence. The worst case of a 20,000-character input takes a fraction of a
second.

**Metrics** are structured log events on the `architectos.metrics` logger (`metric`,
`metric_kind` counter or observation, `value`, `labels`): `requirements.analyze`,
`requirements.analyze.duration_ms`, `requirements.extracted`, `requirements.ambiguous`,
`requirements.conflicting`, `requirements.incomplete` (`status`), `requirements.ready`,
`requirements.promoted`, `requirements.llm.calls` / `requirements.llm_failure` (`source`,
`reason`) and `requirements.llm.input_tokens` / `output_tokens` / `latency_ms`, all labelled with the
`source` (`provider/model`). Labels must be short lowercase
identifiers, so requirement text can never become a label.

## Audit

`requirement.created`, `requirement.version_created` (every change), `requirement.updated` (field
names), `requirement.status_changed` (from, to), `requirement.deleted`, `requirement_set.created`
(number, count, `planning_input_sha256`). Identifiers and field names only: requirement text and
change reasons never reach the audit log or the application logs.
