# Deterministic validation engine

Milestone 6. The engine checks one architecture revision (the canonical
[Architecture IR](architecture-ir.md)) against a set of rules, the project's requirements and its
architecture policy, and reports findings and requirement verdicts. It is deterministic and uses no
language model: the same inputs always give the same result and the same fingerprint. Decisions:
[ADR-011](../adr/ADR-011-deterministic-validation.md). API: [docs/api/validation.md](../api/validation.md).

## Layout

| Where | What |
|---|---|
| `core/domain/validation/results.py` | The result contract: `Finding`, `RequirementResult`, `RuleFailure`, `Limitation`, `Summary`, `RuleSet`, `ValidationResult` |
| `core/domain/validation/runs.py` | `ValidationRun` and its lifecycle, `RunInputs`, `RunReport` (a stored run as read back) |
| `core/domain/validation/options.py` | `ValidationConfig` (what a request may choose), `RevisionInfo` |
| `core/domain/validation/ports.py` | `ValidationEngine`: what the service needs from the engine (the domain never imports `engines`) |
| `core/domain/validation/validation_service.py` | Use cases: validate, list runs, read a run, page through findings, describe the rules |
| `core/domain/projects/policies.py` | `ArchitecturePolicy`, the project's typed policy |
| `engines/validation/engine.py` | The rule contract (`RuleMeta`, `Rule`, `Outcome`, `ParamSpec`), the `Registry`, the orchestrator `validate()` |
| `engines/validation/context.py` | `ValidationContext`: the immutable inputs of one validation, with the shared `Topology` |
| `engines/validation/rules/*.py` | The rules (below) |
| `engines/validation/registry.py` | `default_registry()`: every rule this version ships |
| `engines/validation/service.py` | `DeterministicValidationEngine`, the port's implementation |
| `persistence/repositories/validations.py` | Runs and findings (append-only tables, migration 0011) |
| `apps/api/routes/validations.py` | The HTTP endpoints |

## Execution

1. The API authenticates the caller; the service resolves project → membership → permission
   (`architecture.validate`) inside its transaction, holding the project row (`FOR SHARE`), then
   loads the architecture (which must not be archived) and the revision (the current one unless
   another is asked for).
2. It loads the project's live requirements (every status; at most 5,000) and the project's policy.
3. The engine builds a read-only `ValidationContext`, checks the requested configuration against
   the registry (`InvalidValidationConfig` → `422`, nothing stored), and runs the selected rules one
   by one in rule-id order.
4. The result is normalized (sorted, deduplicated) and fingerprinted; the run is stored
   `completed` with its findings, or `failed` with a safe error, in the same transaction as the
   audit entry `architecture.validated`.

Runs are synchronous (`pending` and `running` exist for a future worker). A rule that raises, or
returns something malformed, becomes a `RuleFailure` of that rule (`unexpected_error`,
`invalid_output`), logged with the rule id; the other rules still run and the run completes with
the failure listed: the absence of findings from a failed rule proves nothing. An engine-level
crash stores a `failed` run (`engine_error`); a storage failure rolls everything back.

## Result contract

- **Finding**: `rule_id`, `rule_version`, `code`, `severity`, `category`, `title`, `explanation`,
  `remediation`, `entity_ids` (nodes and connections), `field_paths` (e.g. `configuration.tls`),
  `expected`, `actual`, `evidence` (label/value pairs), `blocking`, `requirement_id`, `policy_rule`.
  Its `id` (`fnd_` + 20 hex) is derived from the rule, code, entities, fields and requirement, so
  the same finding on the same content has the same id in every run. Text is bounded (2,000
  characters), references to 200, evidence to 50 items.
- **Requirement verdict**: `satisfied`, `violated`, `not_verifiable` (never a pass; the reason says
  why) or `not_applicable`, with the requirement's reference and version, the elements concerned
  and the evidence.
- **Rule failure**: a rule that could not execute. **Limitation**: something no rule of the run
  could check (`catalog_unavailable`, `no_policy`, `requirements_not_provided`).
- **Summary**: counts by severity, category and verdict, blocking findings and rule failures;
  always derived from the findings, never stored separately in the engine. No score is computed.
- **Rule set**: the profile and a version hashed from every rule's (id, version): changing a rule
  changes the rule-set version.

### Severity

| Severity | Meaning |
|---|---|
| `critical` | A requirement of critical priority is violated |
| `high` | The architecture breaks a policy rule, a high-priority requirement, or waits on itself (synchronous cycle) |
| `medium` | A likely defect: disconnected component, contradictory configuration, untraced requirement |
| `low` | Worth fixing: empty boundary, a value that cannot be shown to comply, a stale reference |
| `info` | Context: an outdated requirement reference, a revision stored in an older schema |

**Blocking** marks findings that should stop a design from moving forward: policy violations and
violations of critical-priority requirements. Requests may re-grade the findings of non-mandatory
rules (`severityOverrides`); a finding keeps its id when re-graded.

## Determinism

For the same revision content, rule-set version, requirements (ids, versions, statuses), policy and
configuration the engine returns an equal `ValidationResult` with the same `fingerprint`:

- rules run in id order and do not share mutable state; the context is immutable;
- findings, verdicts, failures and limitations are sorted canonically (findings most severe first)
  and deduplicated; every set is sorted before it is hashed or shown;
- no clock, random value or database id takes part in a result (timestamps and run ids belong to
  the run, not the result);
- the context fingerprint covers the revision (architecture, number, content hash, stored schema
  version), the requirements, the policy and the configuration.

## Rules

Profiles: `default` and `strict` (every rule is in both today; `strict` exists so a stricter set can
be added without changing what `default` means). Mandatory rules always run and cannot be re-graded.

| Rule | Category | Default severity | Reports |
|---|---|---|---|
| `structure.disconnected-component` | structure | medium | A component with no connection (when there are at least two) |
| `structure.empty-boundary` | structure | low | A boundary containing nothing |
| `structure.synchronous-cycle` | structure | high | Components calling each other synchronously in a cycle (parameter `include_unstated`) |
| `structure.deprecated-dependency` | structure | medium/low | A staying component depending on a deprecated one; a deprecated one still calling others |
| `structure.schema-version` (mandatory) | structure | info | A revision stored in an older IR schema, upgraded on read |
| `completeness.unknown-values` | completeness | low | Configuration values marked unknown, per element |
| `configuration.replicas-autoscaling` | configuration | medium | Replicas outside the autoscaling range |
| `configuration.availability-zones` | configuration | medium/low | Multi-AZ with one zone; several zones without multi-AZ |
| `configuration.backups` | configuration | medium/low | Backups kept for 0 seconds; a retention without backups |
| `configuration.retries-without-timeout` | configuration | medium | Retries on a waited-on connection with no timeout |
| `configuration.dead-letter` | configuration | low | A dead-letter setting on a non-consuming connection |
| `policy.technology` (mandatory) | policy | high, blocking | Prohibited technologies; technologies outside the allowed list; unstated ones (low) |
| `policy.region` (mandatory) | policy | high, blocking | Regions outside the allowed list; unknown ones (low) |
| `policy.tls` (mandatory) | policy | high, blocking | Communicating connections with `tls` false; unstated ones (medium) |
| `policy.component-count` (mandatory) | policy | high, blocking | More components than the policy allows |
| `requirements.traceability` | requirements | medium | Requirements in force that nothing references; references to unknown, retired or older requirement versions |
| `requirements.verdicts` | requirements | by priority | A verdict for each requirement in force; a finding per violation |

The IR's own invariants (unique ids, existing endpoints, containment without cycles, property types
and ranges) are enforced when an architecture is built and are not repeated as rules.

### Which requirements are verifiable

Only from values the architecture states:

| Requirement | Checked against |
|---|---|
| `regions` (operational regions, compliance data residency) | The effective region of each concerned component (its own, else its nearest boundary's); data residency concerns components holding data |
| `storage` | The provisioned `storage_bytes` of databases and object stores, summed |
| `retention` | The `retention_seconds` of every component that states one |
| Encryption in transit (security, category `encryption`, statement naming transit, TLS, SSL or HTTPS) | `tls` or an encrypted protocol on every communicating connection |

Everything else is `not_verifiable` with its reason: latency, throughput, availability, RPO/RTO,
budgets and user counts need the capacity, simulation and cost engines; functional requirements
have no structured constraint; encryption at rest is not described by the IR. A requirement
scoped to components the check does not concern (data residency scoped to an API) is
`not_verifiable`, never checked against other components. Verdicts are not compliance
attestations.

## Policy

`ArchitecturePolicy` (per project, `PUT /projects/{id}/architecture-policy`, owners and admins):
`allowed_technologies`, `prohibited_technologies`, `allowed_regions`, `require_tls`,
`max_components`. Empty constrains nothing. The run stores the policy it used.

## Persistence

`validation_runs` (one row per run: revision reference and content hash, profile, status, actor,
timestamps, rule set, fingerprints, summary, verdicts, failures, limitations, inputs, error) and
`validation_findings` (one row per finding, in canonical order, with filter columns and the whole
finding). Both are append-only (triggers), and reference their architecture, revision and run
through same-project foreign keys. Findings are inserted in batches of 500 in the run's
transaction.

## Authorization

Reading runs and findings needs `architecture.read` (viewers and up); running needs
`architecture.validate` (members and up); the policy needs `project.policy_update` (owners and
admins). Every lookup goes project → architecture → run, and findings are read by project and run:
a run of another architecture, project or tenant is `404`. Organization, project and actor come
from the path and the session, never from the body (unknown fields are refused).

## Limits

| Limit | Value |
|---|---|
| Architecture size | 1,000 nodes, 5,000 connections (IR) |
| Requirements considered | 5,000 (more: the run fails `too_many_requirements`) |
| Selected rules, parameter maps, overrides | 100 each; 20 parameters per rule; text parameters 200 characters |
| Findings page | 500 |
| Runs | 120 per user per hour |
| Request body | The API's global body limit |

Measured on the largest architecture the IR allows with a policy nearly everything breaks: 14,093
findings; the engine takes about 0.2 s and the whole request, storage included, about 1.3 s. Reads
of a run, a findings page and the run list cost a fixed number of statements whatever the number
of findings. Rules execute in the request (no separate time limit); their cost is linear in the
number of elements.

## Security

- Rules are code in this package, registered explicitly: no dynamic imports, no expressions, no
  user code. Configuration is data validated against each rule's declared parameters.
- Findings are built from named IR fields; settings kept in `extra`, `metadata` and descriptions
  never reach a finding. Errors carry fixed messages, never exception text; audit entries carry ids
  and counts.

## Known limitations

- No component catalog: configuration is checked against the IR's property definitions only.
- Availability zones are not interpreted against regions (naming differs between providers).
- Verdicts cover regions, storage, retention and encryption in transit only.
- Runs are synchronous; a very large architecture holds the project row (shared) for the run.
- Runs are immutable: a background worker will need a migration to move them through `running`.

## Repository audit

Before any change (phase 0): `engines/validation/*`, `engines/constraints/*`,
`core/domain/components/*`, `core/domain/projects/policies.py`, `knowledge/*.yaml`,
`persistence/models/validation.py` and `apps/api/schemas/validation.py` existed but were empty; no
validation framework, catalog or policy existed to reuse or duplicate. The permission
`architecture.validate` existed and was reused. The IR already enforces structural integrity at
construction, so stored revisions are always valid. The web app proposed a project-level report
with 0–100 health scores and finding triage (see the frontend contract). Reused: the Architecture
IR and its `Topology`, the requirement and project domains, the unit of work, project access and
locking, the audit log, the rate limiter, pagination cursors, the error envelope and the security
sweeps. The empty placeholder files were filled rather than new parallel ones created.

## Final review

Two independent reviews (security, correctness) of the milestone's changes. Security: no critical
or high issue; findings are now read scoped by project as well as run (defense in depth).
Correctness: one bug, fixed: a requirement scoped to components a check does not concern was
checked against those components (a data-residency requirement scoped to an API could pass while
the database was elsewhere); it is now `not_verifiable`. Confirmed sound: determinism (ordering,
fingerprints, stable finding ids), the cycle detection, unit conversions, the stored-run round
trip and pagination.

## Adding a rule

1. Write a class with a `meta = RuleMeta(...)` (a new, stable id; version 1; category; default
   severity; profiles; inputs; parameters) and `evaluate(context, parameters) -> Outcome`, building
   findings with `engines/validation/findings.py`. Read only the context; report named fields.
2. Add it to a module's `RULES` and, for a new module, to `default_registry()`.
3. Changing what an existing rule reports means a new `version`.
4. Test the finding content (entities, fields, evidence), the quiet case, and determinism.

## Tests

```
make test-unit          # tests/unit/validation (engine, rules, service), tests/unit/projects
make test-integration   # tests/integration/api/test_validations.py, test_architecture_policy.py
make test-security      # sweeps, documentation, traceability (test_traceability_validation_engine.py)
make migrate-check      # migrations 0010 and 0011
```
