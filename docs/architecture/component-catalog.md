# Component catalog

The component catalog is ArchitectOS's knowledge base of infrastructure technologies: one
machine-readable **specification** per technology, versioned, every claim with its **provenance**,
and deterministic **constraint evaluation** of architecture configurations against them.

> The catalog stores known facts and explicit assumptions. Deterministic engines evaluate
> configurations. A language model may explain or propose, but must not invent component limits,
> performance figures or capabilities.

**Specifications state documented facts and their sources; they do not guarantee the performance
or capacity of a deployment.** A specification never states the throughput a configuration will
reach, never states a price, and never presents an estimate as a measurement. What no source states
stays **unknown** — never zero, never assumed from another product of the category.

Code:
- `core/domain/components/`: the contract — `entities.py` (categories, support status, provenance),
  `capabilities.py`, `constraints.py`, `specifications.py`, `repository.py` (the catalog and its
  versions), `evaluation.py` (outcomes and findings), `component_service.py`, `schema.py`.
- `engines/constraints/`: the evaluator (`evaluator.py`) and the engine (`service.py`).
- `persistence/component_catalog.py`: reading the catalog's files; `make catalog-lock`.
- `knowledge/components/`: the specifications; `core/schemas/component.schema.json`: their
  published JSON Schema (`make schemas`).
- `engines/validation/rules/components.py`: the validation rule; `apps/api/routes/components.py`:
  the HTTP contract ([docs/api/components.md](../api/components.md)).

Decided in [ADR-019](../adr/ADR-019-component-catalog.md).

## Purpose and scope

In scope: a data-driven catalog of technologies in six categories; per-technology capabilities,
configuration fields, capacity dimensions, scaling methods, failure modes, signals, security
properties, billing dimensions, operational considerations and constraints, each with provenance;
versioned specifications whose versions every evaluation records; deterministic evaluation of a
node's configuration against its specification (in validation runs and on its own).

Out of scope: live inventory discovery, telemetry, benchmarking, throughput predictions from
generic specifications, prices (the Cost Engine's pricing snapshots own them), provisioning,
automatic remediation, a separate knowledge graph, and production-readiness certification.

## Catalog architecture

```text
knowledge/components/<dir>/<entry>.yaml               reviewed files (untrusted input when read)
knowledge/components/<dir>/history/<entry>@<n>.yaml   older versions, kept readable
knowledge/components/catalog.lock.json                every published version's content hash
        │  persistence/component_catalog.py: safe YAML (no aliases), bounded, in place
        ▼
ComponentSpecification ──► ComponentCatalog (read-only; versions 1..N; lock checked)
        │                         │
        │                         ├─► GET /components…            (any signed-in user)
        │                         ├─► POST …/evaluate             (a configuration, nothing stored)
        ▼                         ▼
engines/constraints ◄── node.component (the IR's catalog reference; never matched by name)
        │
        └─► validation rule configuration.component-constraints (validation runs record the versions)
```

The catalog is loaded and checked once at startup; a broken catalog stops the application instead of
serving partial data. There is one architecture model: a specification's configuration fields are
Architecture IR properties, and a node refers to a specification by its `component` path.

## Specification schema

A specification (`core/domain/components/specifications.py`, JSON Schema in
`core/schemas/component.schema.json`):

| Field | Meaning |
|---|---|
| `id`, `version` | the catalog path (`<category directory>/<entry>`) and the version; `ref` is `id@version` |
| `name`, `description`, `aliases` | display and search only |
| `category`, `node_kinds` | a registered category and the IR node kinds it may model |
| `technology`, `technology_versions` | the IR technology identifier; the versions the claims were checked for |
| `provider`, `hosting` | e.g. `community`/`aws`; `managed` or `self_hosted` (or unstated) |
| `support_status`, `replaced_by` | `supported`, `partial`, `planned`, `deprecated` (with its successor) |
| `capabilities` | vocabulary ids with a state: `native`, `requires_configuration`, `requires_external`, `unsupported`, `unknown` |
| `configuration` | IR properties that matter: `required`, `user_configurable`, `engines`, a `default` only when documented |
| `capacity` | dimensions with a unit (capacity units) and a scope; a `value` only with its basis |
| `scaling` | methods with a state, requirements, limitations, what they affect, the engines that evaluate them |
| `failure_modes` | description, impact, preconditions, the signals that show it, mitigations — possibilities, not predictions |
| `signals` | metric, log, trace, health check or event; `native`, `requires_instrumentation` or `requires_external` |
| `security` | properties the technology supports and the IR property that says whether a deployment enables it |
| `billing` | what it is billed by, in the Cost Engine's pricing units — never a price |
| `operations` | areas of operational responsibility (`provider`, `operator`, `shared`) — no complexity score |
| `constraints` | typed limits (below) |
| `sources` | name, reference, version or publication date, and when the claims were checked |

Support status is checked, not declared freely: a `planned` entry claims nothing; `partial` claims at
least its capabilities; `supported` states its capabilities, configuration, failure modes, signals
and security properties.

## Supported categories and entries

Categories (`CATEGORIES`, a registry): `compute`, `database`, `messaging`, `storage`, `networking`,
`observability`. Entries (39):

| Status | Entries |
|---|---|
| `supported` | `databases/postgresql` (18), `databases/redis` (7.4), `messaging/kafka` (4.1), `messaging/aws-sqs`, `compute/aws-lambda` |
| `partial` | `storage/aws-s3` |
| `planned` | compute: `virtual-machine`, `container`, `kubernetes`, `serverless`, `google-cloud-run`, `aws-ecs`; databases: `mysql`, `mongodb`, `aws-dynamodb`, `cassandra`, `scylladb`, `clickhouse`, `elasticsearch`; messaging: `rabbitmq`, `nats`, `aws-sns`, `google-pubsub`, `pulsar`; storage: `google-cloud-storage`, `azure-blob-storage`, `aws-efs`; networking: `load-balancer`, `api-gateway`, `cdn`, `dns`, `waf`, `service-mesh`; observability: `prometheus`, `grafana`, `opentelemetry`, `jaeger`, `loki`, `elk-stack` |

## Provenance: documented, estimated, measured and unknown

Every claim carries a `Provenance`, and the kinds are never mixed up:

- `documented`: stated by a cited source (`sources`, with the date it was retrieved). Every
  constraint's limit and every configuration default must be documented.
- `user_configured`: stated by a person for an architecture — not independently verified.
- `measured`: observed on a system, citing the measurement — valid for that system only.
- `estimated`: from a model or stated assumptions (which it must state) — never a measurement.
- `inferred`: reasoned from documented facts, with the reasoning stated — never a documented fact
  (e.g. "connections beyond max_connections cannot be served").
- `unknown`: no evidence. The claim stays unknown; an evaluation depending on it says
  `cannot_evaluate`.

How to read values: a capacity `value` with `documented` provenance is the source's statement for
the stated scope, not a performance guarantee; a value without one is read from the architecture's
configuration (`property`) or is unknown. A signal or security property says what the technology
supports, not what a deployment collects or enables.

## Specification versioning

- A specification has a stable `id` and versions 1..N without gaps; the latest is current; older
  versions stay in `history/` and remain readable (`GET …?version=n`).
- `catalog.lock.json` records every published version's content hash. The catalog refuses to load a
  version edited in place (`changed_without_new_version`), a removed one
  (`published_version_removed`), or one not yet recorded (`not_locked`). A change is a new version.
- The content hash covers the canonical specification without empty or absent values, so a new
  optional field of the contract changes no existing hash.
- Every evaluation records the versions it used (`ref` → content hash) and the catalog fingerprint;
  a validation run stores them in its inputs (`inputs.components`); the engine can re-evaluate
  against those exact versions.
- Deprecated specifications stay readable and name their successor; evaluating one gives a
  `warning`.

## Constraint evaluation outcomes

Constraint types: `hard_limit`, `configurable_limit` (a documented default or quota that can be
raised), `conditional_limit` (only under stated conditions), `recommended_range`,
`unsupported_configuration`, `unknown` (a limit exists but is undocumented here). A recommendation or
raisable default is at most `medium` severity: it is never treated as a hard limit.

| Outcome | When |
|---|---|
| `pass` | the configured value satisfies the documented constraint |
| `warning` | a recommendation or raisable default is not met; the specification is deprecated |
| `violation` | a documented limit or unsupported value; a node kind the specification does not model |
| `cannot_evaluate` | the value is not stated or marked unknown, a condition cannot be decided, the limit is undocumented, the node states no version for a version-specific constraint, the specification is planned, the component is not in the catalog — never a pass |
| `not_applicable` | a condition does not hold, or the constraint was checked for another technology version |

Each finding has a stable id (`cst_…`, from the component, node and check), the specification
version, the constraint type, severity (violations and warnings), property, actual and expected
values, unit, explanation, remediation and the constraint's provenance. Only documented constraints
are evaluated: no throughput is concluded from CPU, memory or connections.

## Example: a complete specification

`knowledge/components/messaging/aws-sqs.yaml` (version 2; version 1, `planned`, is in `history/`),
abridged only in the repeated source list:

```yaml
id: messaging/aws-sqs
version: 2
name: Amazon SQS
category: messaging
technology: aws-sqs
node_kinds: [queue]
support_status: supported
description: Amazon Web Services' managed message queue service.
provider: {name: aws, service: Amazon Simple Queue Service}
hosting: managed
capabilities:
- {id: message_queue, state: native, provenance: {kind: documented, sources: [metrics]}}
- {id: dead_letter_queue, state: native, provenance: {kind: documented, sources: [metrics]}}
- id: encryption_at_rest
  state: native
  note: SSE-SQS or SSE-KMS; whether a queue uses it is its configuration.
  provenance: {kind: documented, sources: [sse]}
configuration:
- property: retention_seconds
  engines: [validation, capacity]
  default: 345600
  description: Messages are retained for 4 days by default.
  provenance: {kind: documented, sources: [quotas]}
- {property: encryption_at_rest, engines: [security]}
capacity:
- id: message_size
  unit: B
  scope: message
  value: 1048576
  description: The maximum message size (1 MiB); larger payloads need the Extended Client Library and Amazon S3.
  provenance: {kind: documented, sources: [quotas]}
failure_modes:
- id: backlog_growth
  description: Messages accumulate faster than consumers process them.
  impact: Processing is delayed; messages older than the retention period are no longer available.
  signals: [messages_visible, oldest_message_age]
  mitigations: [More consumers., An alarm on the visible messages.]
  provenance: {kind: documented, sources: [metrics]}
signals:
- id: messages_visible
  type: metric
  availability: native
  collection: CloudWatch AWS/SQS ApproximateNumberOfMessagesVisible
  provenance: {kind: documented, sources: [metrics]}
- id: oldest_message_age
  type: metric
  availability: native
  unit: s
  collection: CloudWatch AWS/SQS ApproximateAgeOfOldestMessage
  provenance: {kind: documented, sources: [metrics]}
security:
- id: encryption_at_rest
  state: native
  property: encryption_at_rest
  note: SSE encrypts message bodies, not queue or message metadata.
  provenance: {kind: documented, sources: [sse]}
constraints:
- id: retention_period
  type: hard_limit
  property: retention_seconds
  comparison: between
  minimum: 60
  maximum: 1209600
  severity: high
  description: Message retention is between 60 seconds and 14 days.
  remediation: Choose a retention between 60 and 1,209,600 seconds; keep longer history elsewhere.
  provenance: {kind: documented, sources: [quotas]}
sources:
- id: quotas
  name: Amazon SQS message quotas
  reference: https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/quotas-messages.html
  retrieved: 2026-09-28
- {id: metrics, name: Available CloudWatch metrics for Amazon SQS, reference: "…", retrieved: 2026-09-28}
- {id: sse, name: Encryption at rest in Amazon SQS, reference: "…", retrieved: 2026-09-28}
```

What it does not say is deliberate: no billing dimension (none was verified), no throughput, no
statement that encryption is on by default (the source cited does not say so).

## Example: a constraint evaluation

A queue node referring to `messaging/aws-sqs` with `retention_seconds: 30`, in a validation run or
through `POST /components/messaging/aws-sqs/evaluate`:

```text
retention_period  hard_limit  violation  severity high
  retention_seconds is 30; Message retention is between 60 seconds and 14 days.
  expected: between 60 and 1209600 s   evidence: documented (quotas)   specification: messaging/aws-sqs@2
```

With `retention_seconds: 3600`: `pass`. With the property not stated: `cannot_evaluate` ("not stated
in the architecture"). The ARCH-COMP-001 example — PostgreSQL with 8 CPUs, 32 GB, 1 TB and 2,000
connections — produces **no finding**: the PostgreSQL specification documents no limit for those
properties, so nothing passes, fails or is predicted.

## How downstream engines consume component data

- **Validation Engine**: rule `configuration.component-constraints` (input: catalog) reports the
  constraint engine's evaluation of each node that refers to a component; `info` findings keep what
  cannot be evaluated visible; the limitation `components_not_referenced` states which nodes were
  not checked; the run records the specification versions. Without a catalog:
  `catalog_unavailable`, as before.
- **Capacity, cost, reliability, security, observability, simulation and evolution engines**: they
  do not read the catalog yet, and none keeps a catalog of its own. Their models read the
  architecture's declared configuration; the catalog's documented values (e.g. SQS message size,
  Lambda timeout) are not inputs of those models, and no throughput is inferred from a
  specification. Connecting them is future work, one engine at a time, each citing the versions it
  reads.
- **Language models**: may retrieve and explain specifications; their proposals are validated
  against the catalog and the deterministic rules.

## Adding a component

1. Choose the category directory and an entry id (`knowledge/components/<dir>/<entry>.yaml`).
2. State identity, `node_kinds`, `provider`, `hosting` and `support_status: planned` if nothing is
   specified yet.
3. For each claim, fetch the official source; cite it in `sources` with the date retrieved. What the
   source does not state stays out, or `unknown`. Promote to `partial`/`supported` only when the
   required sections are stated.
4. `make catalog-lock` records the version; the tests check the file, the schema and the lock.

## Adding or updating a constraint

1. The constraint bounds one IR property the specification's node kinds have; add the property to
   the IR first if it does not exist (never a property of the catalog's own).
2. Choose the type the source supports: a recommendation is `recommended_range` (at most `medium`);
   a raisable quota is `configurable_limit`; a limit under conditions is `conditional_limit` with
   its `conditions`; an existing but undocumented limit is `unknown`.
3. Cite the source (`documented`), the remediation, and the versions it was checked for.
4. Any change to an existing specification: copy the current file to `history/<entry>@<n>.yaml`,
   increment `version`, then `make catalog-lock`. The lock refuses an edit in place.

## Known unsupported technologies and fields

- 33 of the 39 entries are `planned`: nothing is claimed about them; nodes referring to them get
  `cannot_evaluate`. `storage/aws-s3` is `partial` (capabilities, capacity, security).
- No billing dimensions are stated yet (none was verified); prices never are.
- Some documented limits have no IR property and are capacity dimensions only (SQS message size,
  Lambda timeout and payload, S3 object size), not evaluated constraints.
- Capacity dimensions use the capacity units: counts without a unit (partitions, queue depth) are
  not expressible yet.
- Recorded evidence is the source as retrieved on its date; sources change (e.g. SQS's message size
  is now 1 MiB) and a new version records a change.
- Separate endpoints for capabilities, constraints and provenance are not provided: they are
  sections of the specification response.

## Security of the catalog

Specification files are untrusted input: YAML is read with a safe loader that also refuses aliases,
files are bounded (256 KB, 2,000 files) and never followed through links, every key is checked
(unknown keys are refused), numbers are exact (no floats), and nothing in a specification is
executed. The API only reads the catalog; there is no mutation endpoint. Configuration evaluation is
rate-limited and stores nothing; architecture evaluations run inside validation runs under project
authorization.

## Tests

```sh
uv run pytest tests/unit/components                       # contract, catalog, files, constraints, evaluation, validation
uv run pytest tests/integration/api/test_components.py tests/integration/api/test_validations.py
uv run pytest tests/security/test_traceability_component_catalog.py
make lint typecheck test-unit test-integration test-security test-eval migrate-check
```

## Repository audit

Phase 0 found every component, constraints and knowledge file an empty placeholder
(`core/domain/components/*`, `engines/constraints/*`, `knowledge/components/**`,
`persistence/models/component.py`, `core/schemas/component.schema.json`). Reused: the IR's
`Node.component` reference and property specifications, the capacity units, the Cost Engine's
pricing units, the Validation Engine's severity, rules and run storage. Still empty placeholders:
`engines/constraints/{compatibility,rules,thresholds}.py`, `persistence/models/component.py`,
`scripts/seed_components.py` (the catalog lives in files, not tables).

## Final review

- Specifications are data, reviewed and versioned; published versions are never rewritten.
- Every claim has provenance; documented claims cite a source checked on a stated date.
- Unknown stays unknown: evaluation says `cannot_evaluate`, never pass.
- One architecture model: configuration fields are IR properties; nodes link by `component`.
- No throughput, price or production-readiness is claimed.
