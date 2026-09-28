# Components API

The component catalog: machine-readable specifications of infrastructure technologies, versioned,
every claim with its provenance, and the evaluation of a configuration against a specification's
documented constraints. The catalog is reference data shared by every organization — it holds no
tenant's data — so any signed-in user may read it. **No endpoint changes it**: a specification
changes by review of its file and a new version ([the component catalog](../architecture/component-catalog.md)).

An entry's presence is not a claim that it is supported: `supportStatus` says how far it is
specified (`supported`, `partial`, `planned`, `deprecated`). Nothing here states throughput or
performance a configuration will achieve, and nothing here is a price.

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `GET /components/categories` | signed in | | `200 {categories, catalogFingerprint}` | |
| `GET /components?category=&status=` | signed in | | `200 {components, catalogFingerprint}` | `422 validation_error` |
| `GET /components/{directory}/{entry}?version=` | signed in | | `200 ComponentSpecification` | `404 component_not_found`, `422 validation_error` |
| `GET /components/{directory}/{entry}/versions` | signed in | | `200 {component, current, versions}` | `404 component_not_found` |
| `POST /components/{directory}/{entry}/evaluate` | signed in | `{nodeKind, configuration: {values, unknown}, version?, technologyVersion?}` | `200 ConstraintEvaluation` | `404 component_not_found`, `422 invalid_architecture, validation_error`, `429 rate_limited` |

Every endpoint answers `401` without a valid session. An architecture's components are evaluated
as part of its [validation runs](validation.md) (rule `configuration.component-constraints`), under
the project's authorization; the evaluations here store nothing.

## Categories and entries

`GET /components/categories`: each category (`compute`, `database`, `messaging`, `storage`,
`networking`, `observability`) with its catalog `directory`, the Architecture IR `nodeKinds` its
components may model, the number of entries and their count by support status.

`GET /components`: the current version of each entry, by id — `{id, version, ref, name, category,
technology, nodeKinds, supportStatus, provider {name, service}, hosting, aliases, replacedBy}`.
`id` is the catalog path a node's `component` refers to (e.g. `databases/postgresql`); `ref` is
`id@version`, what an evaluation records having used. `aliases` are for display and search only:
nodes are linked by `component`, never by name. The catalog is bounded (at most 1,000 entries), so
the list is not paged.

## A specification

`GET /components/{directory}/{entry}` (the current version, or `?version=n`):

- `capabilities`: `{id, state, requires, note, provenance}` with `state` `native`,
  `requires_configuration`, `requires_external`, `unsupported` or `unknown`;
- `configuration`: the Architecture IR properties that matter for the technology,
  `{property, required, userConfigurable, engines, default, description, provenance}` — a
  `default` only when documented, `null` otherwise;
- `capacity`: `{id, unit, scope, value, property, assumptions, description, provenance}` — a
  `value` only with its basis (documented, measured or estimated, with assumptions);
- `scaling`, `failureModes`, `signals` (how the technology provides a signal, not whether a
  deployment collects it), `security` (what the technology supports; the configuration says what
  is enabled), `billing` (what it is billed by, never a price), `operations`;
- `constraints`: `{id, type, description, property, comparison, limit, minimum, maximum, values,
  severity, conditions, technologyVersions, remediation, provenance}` with `type` `hard_limit`,
  `configurable_limit`, `conditional_limit`, `recommended_range`, `unsupported_configuration` or
  `unknown`;
- `sources`: `{id, name, reference, version, published, retrieved}`; every `documented` claim's
  `provenance.sources` names them;
- `contentHash`, `technologyVersions`, `description`, and `current` (whether it is the current
  version).

Provenance kinds: `documented`, `user_configured`, `measured`, `estimated`, `inferred`, `unknown`.

`GET …/versions`: every version, oldest first, `{version, ref, contentHash, supportStatus}` —
older versions stay readable, so an evaluation that used one can be re-read against it.

## Evaluating a configuration

```json
POST /api/v1/components/messaging/aws-sqs/evaluate
{"nodeKind": "queue", "configuration": {"values": {"retention_seconds": 30}}}
```

```json
{
  "specifications": {"messaging/aws-sqs@2": "…"},
  "catalogFingerprint": "…",
  "findings": [
    {"id": "cst_…", "component": "messaging/aws-sqs", "specification": "messaging/aws-sqs@2",
     "check": "retention_period", "constraintType": "hard_limit", "outcome": "violation",
     "severity": "high", "property": "retention_seconds", "actual": 30,
     "expected": "between 60 and 1209600", "unit": "s",
     "explanation": "retention_seconds is 30; Message retention is between 60 seconds and 14 days.",
     "remediation": "Choose a retention between 60 and 1,209,600 seconds; …",
     "provenance": {"kind": "documented", "sources": ["quotas"], "…": "…"}}
  ],
  "summary": {"pass": 0, "warning": 0, "violation": 1, "cannot_evaluate": 0, "not_applicable": 0},
  "fingerprint": "…"
}
```

- `configuration.values` are Architecture IR node properties as an architecture states them
  (decimals as strings; at most 100); `unknown` lists properties whose value is not known. The IR's
  own rules apply: an unknown property, a wrong type or a property of another node kind is
  `422 invalid_architecture` with the violations.
- Outcomes: `pass`, `warning` (a recommendation or raisable default not met, or a deprecated
  specification), `violation` (a documented limit, an unsupported value, or a node kind the
  specification does not model), `cannot_evaluate` (a value not stated or unknown, an undocumented
  limit, a condition that cannot be decided, a planned specification — never a pass),
  `not_applicable` (a condition that does not hold, or another technology version).
- Only the constraints the specification documents are evaluated. Rate limit: 600 evaluations per
  user per hour.
