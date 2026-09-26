# Architecture IR

## 1. Purpose

The Architecture Intermediate Representation (IR) is ArchitectOS's one canonical, machine-readable
description of a software architecture: its components, how they communicate, how they are
configured, which requirements they answer, what was assumed, and where every value came from.
Every architecture capability (design, capacity, constraints, validation, reliability, security,
cost, observability, simulation, discovery, drift, evolution, migration) reads and writes this one
representation.

## 2. Why it is the source of truth

> The LLM proposes. Architecture IR stores. Deterministic engines calculate. Validators challenge.
> Simulators test. Evidence supports. Humans decide.

- It is **independent** of any LLM response format, HTTP schema or storage: pure, immutable Python
  values in `core/architecture_ir/`, depending only on the standard library.
- It is **structurally validated** before it exists: an `ArchitectureIR` object that could be
  constructed is well-formed (every problem is reported at once, see §10).
- It is **versioned twice**: the format (`schema_version`, §12) and the content (revisions, §13).
- It is **stored as is** (canonical JSON), so what an engine or a person saw can always be shown
  again, byte for byte.

Structural validity says nothing about quality: a valid IR is not claimed to be scalable, reliable
or production-ready. That is the engines' job, with evidence.

## 3. Relationship to the Requirements Engine

The Requirements Engine turns text into canonical requirements and pins them in immutable
requirement sets, from which it builds the Architecture Planning Input
([docs/requirements-engine.md](../requirements-engine.md)). The IR only **references**
requirements (`requirement_refs`: requirement id, optionally a version) on the architecture, on
nodes, on connections and on assumptions; it never copies them. When an architecture is stored,
every reference must name a requirement of the project at an existing version, and a revision
records the requirement set it was designed against. The dependency is one-way: the requirements
domain knows nothing about architectures.

## 4. Relationship to the Architecture Engine

`engines/architecture/service.py` defines the contract: an `ArchitectureGenerator` consumes a
planning input and returns an `ArchitectureProposal` whose content is an `ArchitectureIR`, never a
structure of its own. `check_proposal` refuses output that does not say it was generated, that
claims anything is verified, or that cites a requirement it was not given. A person reviews a
proposal; adopting it creates a revision (`source: system` or `ai`). The engines read the IR
through `Topology` (`core/architecture_ir/topology.py`): indexed, deterministic graph queries
(neighbours, what a node depends on, what depends on it, containment).

## 5. Node model

A node (`node.py`) is a component or a boundary:

| Field | Meaning |
|---|---|
| `id` | Stable identity: 1-128 of `A-Za-z0-9._:/-`, starting with a letter or digit, unique across nodes, connections and assumptions, never derived from the name |
| `kind` | `client`, `cdn`, `load_balancer`, `gateway`, `service`, `worker`, `database`, `cache`, `queue`, `storage`, `observability`, `external`, `boundary` |
| `name`, `description` | Display text (a name need not be unique) |
| `technology` | `{name, version}`, an identifier (`postgresql`, `16`), not a closed list |
| `component` | Optional component-catalog path, e.g. `databases/postgresql` |
| `parent_id` | The boundary containing it (a system, network, region, account, cluster…) |
| `configuration` | Typed values, unknown values and preserved settings (§7) |
| `requirement_refs`, `metadata`, `lifecycle` | Traceability, text labels, `planned` / `active` / `deprecated` |
| `provenance`, `field_provenance` | Where the node, and each of its fields, came from (§9) |

Visual position is **not** part of a node: the layout is stored beside the architecture and never
versioned, so moving a box is never an architecture change.

## 6. Connection model

A connection (`edge.py`, `dependency.py`) joins two nodes; `source_id` initiates, `target_id` is
reached:

| Field | Meaning |
|---|---|
| `kind` | `request` (a call), `publish`, `consume`, `data_access`, `replication`, `dependency` (the source needs the target without communicating: no protocol, no interaction) |
| `protocol` | An identifier: `https`, `grpc`, `postgresql`, `redis`, `kafka`, `amqp`, … |
| `interaction` | `synchronous` (the source waits) or `asynchronous` |
| `bidirectional` | Traffic both ways on one connection (a websocket) |
| `critical` | The source cannot do its job without it (`null`: not stated) |
| `name`, `description` | A label and what flows, e.g. "order events" |
| `configuration`, `requirement_refs`, `metadata`, `provenance`, `field_provenance` | As for nodes |

## 7. Configuration strategy

`configuration` has three parts (`configuration.py`):

- `values`: **known properties**, each with a type, a range and the node kinds it applies to.
  Units are part of the name and canonical (`memory_limit_bytes`, `retention_seconds`,
  `cpu_request_cores`), so a value never needs a unit. Numbers are exact: integers, or decimals
  written as strings (`"0.5"`). Examples: `replicas`, `autoscaling_*`, `cpu_*_cores`,
  `memory_*_bytes`, `instance_class`, `deployment_model`, `region`, `availability_zones`,
  `multi_az`, `storage_bytes`, `max_connections`, `replication_mode`, `backup_*`,
  `eviction_policy`, `persistence`, `partitions`, `replication_factor`, `retention_seconds`,
  `boundary_type`; on connections `timeout_seconds`, `retries`, `tls`, `dead_letter`, `port`,
  and for traffic `traffic_ratio`, `calls_per_request`, `cache_hit_ratio`, `access`, `pool_size`;
  capacity on nodes `throughput_limit_per_second`, `throughput_per_replica_per_second`,
  `cpu_core_seconds_per_request`, `network_bandwidth_bytes_per_second` (see
  [the capacity engine](capacity-engine.md#demand-propagation)); pricing on nodes
  `pricing_service`, `pricing_sku` (else `instance_class` is matched exactly),
  `pricing_storage_sku`, `pricing_conditions`: how a component maps to its organization's
  [pricing snapshots](../api/pricing.md), never inferred when absent (see
  [the cost engine](cost-engine.md#resource-mapping)); reliability on nodes `availability`
  (the component as a whole) and `replica_availability` (fractions, 0.999 = 99.9 %),
  `mtbf_seconds`, `mttr_seconds`, `min_healthy_replicas`, `failure_independence`
  (`independent`/`correlated`/`unknown`), `failover_mode` (`none`/`manual`/`automatic`),
  `failover_seconds`, `redundancy_group`, `redundancy_group_min_healthy`,
  `replication_lag_seconds`, `backup_interval_seconds`: declared, never defaulted.
- `unknown`: known properties whose value is **not known** (discovery could not read it). Unknown
  is never the same as absent, and never filled in.
- `extra`: settings the IR does not recognize, **preserved as found** (bounded; fractional numbers
  kept as text). Engines must not rely on them.

Which technology supports which values is the component catalog's concern, not the IR's.

## 8. Requirement traceability

`requirement_refs` link the architecture, nodes, connections and assumptions to requirements;
`decisions` link architecture decisions (owned by the decisions domain) to the nodes and
connections they concern. Example: the requirement "payments stay available when the notification
provider fails" is referenced by the payment service, the notification service, the broker and the
connection whose `dead_letter` configuration keeps failed notifications.

## 9. Provenance and assumptions

Provenance (`provenance.py`) says where a value came from: `source` (`user_input`, `user_edit`,
`llm_proposal`, `terraform`, `kubernetes`, `cloud_discovery`, `file_import`, `system_default`,
`schema_migration`), `reference` (e.g. `aws_db_instance.orders`), `confidence`, `verified`,
`inferred`, `actor` and `recorded_at`. It can be set on the architecture (the default), on an
element, or on one field (`field_provenance["configuration.replicas"]`); the nearest applies.

- `verified`: confirmed by a person or read from the running system. A model proposal or a system
  default can never be verified: once a person confirms it, it becomes their `user_edit`.
- `inferred`: derived rather than stated or read; never both inferred and verified.
- `confidence` is how sure the producer is that it read or derived the value correctly; never
  proof. A model proposal must state it.

Assumptions (`traceability.py`) are statements taken as true without verification ("peak traffic
is 3x the daily average"), always with their provenance and the elements that rely on them. They
are never turned into facts silently. Edits through the API attribute every field they change to
the editor (`user_edit`).

## 10. Structural invariants

Enforced at construction (`model.py`), all reported at once, each violation naming the element,
its id, the field, the rule and a sentence to act on:

- ids are unique across nodes, connections and assumptions;
- every connection joins two existing, different nodes (no self-connections), neither a boundary;
- the same connection (source, target, kind, protocol) is not stated twice; another kind or
  protocol between the same nodes is another connection;
- **cycles are allowed** (services call each other; replication goes both ways);
- a parent is an existing boundary, and containment has no cycles;
- assumptions and decisions refer only to existing nodes and connections;
- each property is typed, in range and applicable to its node kind; paired bounds are ordered
  (`autoscaling_min_replicas` ≤ `autoscaling_max_replicas`, request ≤ limit);
- an empty architecture is valid: it is where every design starts;
- bounded sizes: 1,000 nodes, 5,000 connections, 500 assumptions and decisions.

`validation.validate(data)` returns the IR or the violations without raising;
`requirement_problems` checks references against the requirements that exist.

## 11. Serialization contract

`serialization.py` writes one canonical JSON form: every field present (`null`, `[]` or `{}` when
empty), collections in id order, sorted keys, decimals as strings, integers as numbers, times in
UTC ISO 8601. Equal architectures have byte-identical JSON and the same `content_hash`
(SHA-256), whatever order their parts were given in. Reading is strict:

- a field the schema does not define is refused (`unknown_field`); unrecognized settings belong in
  `configuration.extra`;
- a missing optional field takes its default; a missing required one is reported;
- numbers are accepted as JSON numbers or decimal strings and converted exactly (`0.1` is
  `Decimal("0.1")`, never a float); `NaN` is refused;
- absurd nesting or size is refused cleanly, never a crash.

The JSON Schema `core/schemas/architecture.schema.json` is **generated** from the same
definitions (`make schemas`); a test fails if it is out of date or disagrees with the code's output.

## 12. Schema versioning

`schema_version` is the version of the **format**, not of an architecture (§13).

- Adding an optional field, a node kind, a connection kind or a configuration property is
  backward compatible: old documents stay valid; the version does not change.
- Changing or removing anything is a new version, with a pure upgrade function from the previous
  one in `versioning.UPGRADES`. Stored documents are upgraded **when read**, step by step, never
  rewritten in place; an upgrade keeps every id and never changes what the architecture says (a
  value it must introduce carries `schema_migration` provenance).
- A document from a newer schema than the code knows is refused, never guessed at.

## 13. Architecture revisioning

A project has many architectures (ADR-010). Each architecture's **metadata** (name, description,
lifecycle status) lives on its record and changes without creating a revision; its **content**
lives in **immutable, numbered revisions** (`core/domain/architecture/versions.py`). Revision n+1
is created from revision n and records its parent, `source` (`user`, `ai`, `discovery`, `import`,
`system`), a generated summary of what changed, an optional reason, the IR schema version, the
content hash, the requirement set it was designed against (carried over from the parent unless
given) and, for a restore, the revision whose content it restores (`restored_from`).

- Revision numbers are per architecture, increase by one and are never reused.
- Edits are based on a revision (`baseVersion`) that must be current; otherwise
  `architecture_version_conflict`: nothing is merged or overwritten silently. The architecture row
  is locked while a revision is written, so concurrent edits of one revision create exactly one.
- A change that leaves the content as it is creates no revision (and succeeds).
- **Restoring** revision k creates a new revision with k's content; nothing in between is erased and
  the current pointer never moves backwards.
- The database refuses any change to a stored revision (append-only trigger) and guarantees the
  current revision exists, belongs to the architecture, that each revision's parent is the previous
  one and that a restore names an earlier revision of the same architecture.
- A stored revision is returned exactly as stored, in its own schema version; reading it for the
  engines upgrades a copy in memory, never the stored snapshot.

## 14. Diff behaviour

`diff.diff(before, after)` (`diff.py`) matches elements by **id**: a renamed node is modified,
never removed and re-added; a changed id is a removal and an addition. Each modified element lists
its changed fields with before and after values (canonical JSON) and a category (`description`,
`kind`, `technology`, `resources`, `configuration`, `placement`, `lifecycle`, `endpoints`,
`semantics`, `metadata`, `provenance`, `traceability`), plus architecture-level, assumption and
decision changes and a one-line summary. Decimals compare by value (`0.250` equals `0.25`). The
same inputs always give the same diff; it serves change review, AI proposals, audit, evolution and
migration planning.

## 15. Persistence strategy

PostgreSQL, no graph database (migrations 0008 and 0009): `architectures` (many per project, with
their metadata and lifecycle, pointing at the current revision through a deferred composite foreign
key; live names unique per project), `architecture_revisions` (append-only;
the IR as canonical JSONB, at most 16 MiB, with schema version, hash, parent, source, summary,
reason and requirement set) and `architecture_layouts` (positions, never versioned). Composite
foreign keys keep an architecture, its revisions, its layout and the referenced requirement set
inside one project. Revisions are written in the same transaction as the architecture's current
pointer, under the project lock and a row lock on the architecture. Business rules live in the
domain, never in the ORM models.

## 16. Downstream engine integration

| Engine | Reads |
|---|---|
| Capacity | nodes, kinds, `replicas`, `autoscaling_*`, CPU and memory, connections and their interaction, workload assumptions |
| Constraints | kinds, technologies, component references, configuration, topology |
| Validation | the whole IR; its findings are about suitability, separate from structural validity |
| Reliability | `critical`, `multi_az`, `availability_zones`, `replication_*`, `dependents_of` |
| Security | boundaries (`trust_zone`, `network`), `tls`, protocols, external nodes |
| Cost | technologies, `instance_class`, sizes, replicas |
| Observability | observability nodes and what reaches them |
| Simulation | a stable revision: its number and content hash identify exactly what was simulated |
| Discovery | produces an IR with `terraform`/`kubernetes`/`cloud_discovery` provenance, `unknown` values and preserved `extra` settings |
| Evolution, migration | the diff between revisions |

No engine defines its own architecture structure.

## 17. Adding a node kind safely

1. Add it to `NodeKind` (`component.py`) with a comment on what it is.
2. Say which configuration properties apply to it (`applies_to` in `configuration.py`).
3. Run `make schemas`, update this document and the web app's component types.
4. Adding a kind is backward compatible (no schema version change); renaming or removing one is
   not (§12).

## 18. Adding a configuration property safely

1. Add a `PropertySpec` in `configuration.py`: a name with its canonical unit, a type, a range,
   the kinds it applies to, a description.
2. Older imports may have preserved the same setting in `extra`; nothing breaks: new documents
   give it in `values`, old ones keep it in `extra` until re-imported.
3. Add it to `RESOURCE_PROPERTIES` in `diff.py` if it is a resource, and run `make schemas`.
4. A new optional property is backward compatible; changing a property's unit or meaning is a new
   schema version with an upgrade.

## 19. Evolving the IR without breaking consumers

- Additive changes only within a schema version; anything else is a new version with an upgrade.
- Readers are strict about structure but tolerant through `extra`, so unrecognized settings from
  newer tools are kept, not lost.
- Consumers rely on ids and the canonical JSON, never on names or ordering they did not ask for.
- The JSON Schema, this document and the tests move together (`make schemas`; the documentation
  and schema tests fail otherwise).

## Examples

A simple API with PostgreSQL (an input document: optional fields may be left out; the stored form
has every field):

<!-- ir-example -->
```json
{
  "schema_version": 1,
  "name": "Orders",
  "nodes": [
    {"id": "web", "kind": "client", "name": "Web app"},
    {"id": "api", "kind": "service", "name": "Orders API", "technology": {"name": "fastapi"},
     "configuration": {"values": {"replicas": 3, "cpu_request_cores": "0.5", "memory_limit_bytes": 1073741824}}},
    {"id": "db", "kind": "database", "name": "Orders DB", "technology": {"name": "postgresql", "version": "16"},
     "configuration": {"values": {"storage_bytes": 100000000000, "multi_az": true}}}
  ],
  "connections": [
    {"id": "web-api", "source_id": "web", "target_id": "api", "kind": "request", "protocol": "https",
     "interaction": "synchronous"},
    {"id": "api-db", "source_id": "api", "target_id": "db", "kind": "data_access", "protocol": "postgresql"}
  ]
}
```

A service with a cache and a queue, traceable to a requirement, with an assumption:

<!-- ir-example -->
```json
{
  "schema_version": 1,
  "name": "Orders with events",
  "requirement_refs": [{"requirement_id": "0199a7c2-5d1e-7b3a-9f10-2c4d6e8f0a12", "version": 3}],
  "nodes": [
    {"id": "api", "kind": "service", "name": "Orders API",
     "configuration": {"values": {"replicas": 4, "autoscaling_min_replicas": 2, "autoscaling_max_replicas": 12,
                                  "autoscaling_target_cpu_ratio": "0.7",
                                  "availability_zones": ["eu-west-1a", "eu-west-1b"]}},
     "requirement_refs": [{"requirement_id": "0199a7c2-5d1e-7b3a-9f10-2c4d6e8f0a12", "version": 3}]},
    {"id": "cache", "kind": "cache", "name": "Cache", "technology": {"name": "redis", "version": "7.2"},
     "configuration": {"values": {"memory_limit_bytes": 4294967296, "eviction_policy": "allkeys_lru"}}},
    {"id": "events", "kind": "queue", "name": "Order events", "technology": {"name": "kafka"},
     "configuration": {"values": {"partitions": 12, "replication_factor": 3, "retention_seconds": 604800}}},
    {"id": "worker", "kind": "worker", "name": "Fulfilment worker", "configuration": {"values": {"replicas": 2}}}
  ],
  "connections": [
    {"id": "api-cache", "source_id": "api", "target_id": "cache", "kind": "data_access", "protocol": "redis",
     "configuration": {"values": {"timeout_seconds": "0.05"}}},
    {"id": "api-events", "source_id": "api", "target_id": "events", "kind": "publish", "protocol": "kafka",
     "interaction": "asynchronous", "critical": false, "description": "order placed"},
    {"id": "worker-events", "source_id": "worker", "target_id": "events", "kind": "consume", "protocol": "kafka",
     "interaction": "asynchronous", "configuration": {"values": {"retries": 5, "dead_letter": true}}}
  ],
  "assumptions": [
    {"id": "cache-hit-rate", "statement": "80 % of reads are served by the cache.",
     "provenance": {"source": "llm_proposal", "confidence": "0.6"}, "subject_ids": ["api-cache"]}
  ]
}
```

A revision with a configuration change: on the first example, the edit
`[{"type": "change_replicas", "nodeId": "api", "replicas": 6}]` creates revision 2, whose diff
(`diff.diff`) is:

```json
{
  "summary": "1 node modified.",
  "nodes": [{
    "element": "node", "element_id": "api", "change": "modified", "label": "Orders API", "kind": "service",
    "categories": ["provenance", "resources"],
    "fields": [
      {"field": "configuration.replicas", "before": 3, "after": 6, "category": "resources"},
      {"field": "field_provenance.configuration.replicas", "before": null,
       "after": {"source": "user_edit", "reference": null, "confidence": null, "verified": false,
                 "inferred": false, "actor": "user:0199a7c2-0000-7000-8000-000000000001",
                 "recorded_at": "2026-09-26T09:00:00+00:00"},
       "category": "provenance"}
    ]
  }],
  "architecture": [], "connections": [], "assumptions": [], "decisions": []
}
```

A discovered architecture: verified values from Terraform, values it could not read (`unknown`),
settings the IR does not recognize (`extra`) and one inferred value:

<!-- ir-example -->
```json
{
  "schema_version": 1,
  "name": "Production (discovered)",
  "provenance": {"source": "terraform", "reference": "prod.tfstate", "verified": true},
  "nodes": [
    {"id": "vpc-main", "kind": "boundary", "name": "Main VPC",
     "configuration": {"values": {"boundary_type": "network", "region": "eu-west-1"}}},
    {"id": "aws_db_instance.orders", "kind": "database", "name": "orders", "parent_id": "vpc-main",
     "technology": {"name": "postgresql", "version": "15.4"}, "component": "databases/postgresql",
     "configuration": {
       "values": {"instance_class": "db.r6g.large", "storage_bytes": 200000000000, "multi_az": true},
       "unknown": ["backup_retention_seconds", "max_connections"],
       "extra": {"parameter_group": "default.postgres15", "iops": 3000}},
     "provenance": {"source": "terraform", "reference": "aws_db_instance.orders", "verified": true,
                    "actor": "discovery:terraform", "recorded_at": "2026-09-26T08:30:00+00:00"},
     "field_provenance": {"configuration.multi_az": {"source": "system_default", "inferred": true}}}
  ]
}
```

Every block marked as an IR example is read by the real reader in
`tests/unit/architecture_ir/test_ir_documentation.py`, and the diff example is recomputed there,
so these examples cannot drift from the code.

## Working on it locally

`make db-up migrate` starts PostgreSQL and applies the migrations; `make test-unit` runs the IR,
revision, diff and service tests without a database; `make test-integration` the persistence,
concurrency and API tests; `make test-security` the tenant, authentication, audit and documentation
sweeps; `make schemas` regenerates the JSON Schema after a model change.

## Repository audit

Before any change (phase 0), every architecture file in the repository was an empty placeholder:
`core/domain/architecture/*`, `core/architecture_ir/*`, `core/schemas/*.json`,
`engines/architecture/*`, `persistence/models/architecture.py`, `persistence/repositories/architectures.py`,
`apps/api/schemas/architecture.py`, the decisions, components, migrations and simulations domains,
`discovery/*`, `knowledge/*` and `tests/architecture/*`. Permissions (`architecture.read`, `.create`,
`.update`, …) already existed. The only real contract was the web app's proposed one
(`apps/web/schemas/architecture.ts`, `api/architectures.ts`), served by its mock API. Two empty
homes for the same model existed (`core/domain/architecture/{nodes,edges,topology}.py` and
`core/architecture_ir/`); the duplicates were removed so that one model exists. Decided then: the
IR in `core/architecture_ir`, its lifecycle in `core/domain/architecture`; revisions as JSONB
snapshots; the web app's endpoint list (without generation) plus creation from an IR; exact
decimals as strings.

## Definition of done

Each item of the specification's section 23 is mapped to the tests that prove it in
`tests/security/test_traceability_architecture_ir.py`, which fails if a mapped test is renamed or
removed; `test_there_is_one_architecture_model` fails if any other package defines its own node,
edge or architecture class.

## Final review

Reviewed at the end of the implementation, including an independent review that executed
adversarial inputs against the code:

- **Error handling.** Every structural problem is an `invalid_architecture` with all violations
  (element, id, field, rule, message); command errors name the failing command; conflicts,
  not-found and archived projects have their own codes. About twenty adversarial payloads (wrong
  types at every level, huge numbers, `NaN`, 100,000-character ids, cycles, 200,000-deep nesting)
  all end in a 422, never a 500.
- **Security boundaries.** Every repository query is scoped by project; composite foreign keys
  keep an architecture, its revisions, its layout and its requirement set in one project; the
  tenant-isolation, authentication and mass-assignment sweeps cover every architecture endpoint.
  Access is checked before a request body is parsed, so a caller without access cannot make the
  server validate a large document (found in review, fixed). The audit log records identifiers and
  counts only (a canary sweep proves it). Only the create endpoint accepts bodies over 64 KiB.
- **Integrity.** Revisions are append-only in the database; a stale edit is refused (a real race of
  five simultaneous edits creates exactly one revision); an edit that changes nothing creates none.
- **Determinism.** Equal architectures give byte-identical JSON and the same hash whatever the
  order of their parts; the diff is identity-based and reproducible.
- **Schema compatibility.** One schema version so far; the upgrade chain, the refusal of newer
  documents and reading through upgrades are tested; the JSON Schema is generated and checked.

Remaining, by design or for later specifications: the web app still uses its proposed contract
(see [docs/frontend/architecture-contract.md](../frontend/architecture-contract.md)); the
Architecture Engine's generator, the component catalog, discovery connectors and the capacity and
cost engines are not implemented (the IR, its contracts and `Topology` are ready for them); no
endpoint replaces a whole architecture yet (the service supports it for proposals, imports and
discovery).
