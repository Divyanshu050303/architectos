# Migration planning engine

The migration planning engine describes **how to move an architecture from an exact source revision
to an exact target**: the steps, the order they depend on, the data that must move, where downtime
is known, possible or modeled away, the compatibility questions, the risks, the verification
checkpoints and the rollback considerations — each traced to the architecture change, pattern,
constraint, assumption or stored analysis it rests on.

**A migration plan is a proposal for engineering review and authorization.** It never executes a
step, provisions anything, copies or backfills data, switches traffic, rolls back, or changes the
canonical architecture. Approving a plan records that a person with `migration.approve` approved one
exact version; carrying it out belongs to a separate, explicitly authorized workflow. No plan states
a duration, a data volume, a success probability or a risk score, and none claims zero downtime.

Architecture evolution ([evolution-engine.md](evolution-engine.md)) proposes and evaluates target
changes; migration planning describes the transition to a selected target; execution is out of
scope.

| Part | Where |
|---|---|
| Domain (requests, steps, risks, checkpoints, rollback, data, compatibility, plans, versions) | `core/domain/migrations/{values,steps,plans,entities}.py` |
| Change classification | `core/domain/migrations/changes.py`, `engines/migration/changes.py` |
| Patterns | `engines/migration/patterns.py` (registry), `engines/migration/patternbook.py` (the patterns) |
| Step generation | `engines/migration/planner.py` |
| Dependencies and sequencing | `engines/migration/dependency_graph.py`, `engines/migration/sequencing.py` |
| Data, downtime, compatibility | `engines/migration/{data_migration,availability,compatibility}.py` |
| Risks, checkpoints, rollback | `engines/migration/{risk,checkpoints,rollback}.py` |
| Evidence from other engines | `core/domain/migrations/{evidence,loading}.py`, `engines/migration/evidence.py` |
| Versioning and review | `core/domain/migrations/versioning.py` |
| Service, port, persistence | `core/domain/migrations/{migration_service,ports,repository,serialization}.py`, `persistence/repositories/migrations.py`, migration `0020` |
| API | `apps/api/routes/migration_plans.py`, `apps/api/schemas/migration.py`, [docs/api/migration-plans.md](../api/migration-plans.md) |

## Purpose and scope

The engine answers, for one source and one target: what changes, which steps carry it out, in which
order, which steps wait for which, what can run together (nothing, unless a rule says so
explicitly), the risks and downtime implications, the compatibility questions, how data moves and
is verified, which checkpoints must hold before proceeding, how each high-impact step could be
recovered from, and what information is missing before the plan can be trusted. It compares the
supported strategies side by side and leaves the choice to people.

Out of scope, by design: automatic architecture mutation, infrastructure provisioning, cloud or
Kubernetes calls, database migration or backfill execution, live traffic switching, runtime
monitoring, automatic rollback, CI/CD orchestration, autonomous approval, guaranteed zero downtime,
fabricated durations or probabilities, new capacity, cost, reliability, security or observability
calculations, and a second architecture model.

## Source and target revisions

A request names one architecture, its exact **source revision**, and a **target**:

- a **later revision of the same architecture** (`target.revision` greater than `sourceRevision`); or
- an **evolution candidate** (`target.analysisId` and `target.candidateId`) of an evolution analysis
  of that architecture, rebuilt on its exact baseline — which must be the source revision.

A different architecture, an earlier or equal revision, a candidate on another baseline, a candidate
that evolution's validation refused, or one that no longer applies, is refused
(`invalid_migration_request` or a `404`). The request never carries topology: the revisions are read
from the architecture's history, and their content hashes are recorded on the plan
(`source.contentHash`, `target.contentHash`). For a candidate, the target's hash is the candidate
overlay's. The engine checks that each content it is handed is the content the reference names.

## Architecture diff integration

The engine reuses the Architecture IR's own diff (`core.architecture_ir.diff`): no second graph
comparison exists. Each element change (node or connection, added, removed or modified, with its
field changes and their categories) is classified (`MigrationChange`):

- **relevance** — migration-relevant, or metadata only (descriptions, provenance, traceability,
  lifecycle); a plan with only metadata changes has no steps and a `no_changes` finding;
- **aspects** — provisioning, decommissioning, topology, role, technology, routing, protocol,
  grouping, placement, resources, scaling, configuration, security, observability, unrecognized
  settings, unknown values;
- **engines** that read the changed properties (each engine's own declared inputs);
- **interpretation** — supported, needing a person (`manual_interpretation`: a boundary added or
  removed, a component moved to another boundary, an unrecognized or unknown setting, a stateful
  component changing region or zone), or unsupported (`unsupported_change`: a component changing its
  kind, i.e. its role).

Changes for a person and unsupported changes are findings, never steps.

## Supported migration patterns

Patterns are deterministic and versioned (`PatternMeta`: id, version, the changes it applies to, its
evidence, preconditions, capabilities, step types, dependency rules, risks, trade-offs, downtime and
reversibility when its prerequisites hold, data requirements, validation, what it does not
support). Every step names the pattern (with its version) that produced it.

| Pattern | Kind | Supported when |
|---|---|---|
| `reconfigure@1` | template | a modified element's configuration, resources or technology (not a stateful technology change) |
| `provision@1` | template | an added component or connection |
| `decommission@1` | template | a removed component or connection, after verifying nothing uses it |
| `reroute@1` | template | a connection whose endpoints or protocol change |
| `in_place@1` | strategy | always — the documented default |
| `rolling@1` | strategy | at least 2 replicas declared in the source and the target, and `health_check: true` in the target |
| `blue_green@1` | strategy | a load balancer or gateway in front of the component in the target |
| `replication_cutover@1` | strategy | a stateful replacement whose target declares `replication_mode` (asynchronous or synchronous) |
| `canary@1`, `expand_contract@1`, `strangler@1` | strategy | never — registered so a request for them is answered with why |

A stateful replacement is a stateful component changing technology (same id), or one removed while
exactly one of the same kind is added. The plan follows the strategy the request prefers when it is
supported; otherwise in-place, with a `strategy_not_supported` finding naming what is missing.
Strategies are never scored or ranked; every strategy is listed with its trade-offs.

## Migration step contract

A `MigrationStep` has a stable id derived from its key (`stp_…` from e.g. `configure:api`), a type
(prepare, provision, configure, replicate, backfill, verify, cutover, decommission, rollback,
manual_review), a title, the elements it concerns, preconditions, required inputs, the expected
outcome and completion criteria in words, its dependencies (step ids), `parallelizable` (false
unless a rule says so), downtime (`known_downtime`, `potential_downtime` with its conditions,
`modeled_online`, `unknown`), traffic implications, availability considerations, data impact,
reversibility, whether a person must verify it, assumptions, and traces (at least one change and
the pattern). A step is planned, never executed: it holds no command and no execution record.

## Dependency graph semantics

Dependencies are explicit: a connection after its new endpoints are verified, a cutover after the
verification of the connections into its replacement, decommissioning after traffic and data have
moved away, connections before their components. The graph (`DependencyGraph`) reports a dependency
on a step the plan does not contain (`invalid_dependency`) and every group of steps that wait for each
other (`dependency_cycle`, found by an iterative strongly-connected-components search — no recursion
limit). Only a valid graph is sequenced: a topological order with ties broken by step key, in
numbered stages; several steps share a stage only when each is explicitly parallelizable.

Transition rules (`missing_prerequisite`): a step concerning an added element waits for its
provisioning; the decommissioning of a removed element waits for every other step concerning it; a
cutover waits for a verification or preparation, a decommissioning for a verification; a cutover,
an irreversible step and a step with known or potential downtime require a person's verification.
These findings block review; nothing is reordered to hide them.

## Data migration planning

For each stateful replacement, a `DataMigration` states the source and destination, the scope as the
request states it (`dataRequirements`), the method (replication then cutover, or an offline copy
with writes stopped), the initial copy, the replication requirements, how consistency is verified
before the cutover, the cutover's prerequisites, what is retained, the rollback implications and
where data could be lost. A stateful component removed without replacement states that its data is
removed unless retained. **Volume, throughput and duration are never stated**: they are listed as
missing, and the duration is `unevaluable`. An unstated scope (or retention) is a
`missing_information` finding.

## Downtime and compatibility semantics

A step is `modeled_online` only when its pattern's declared prerequisites support it (a rolling
roll-out, a blue-green switch, provisioning what nothing uses yet). An in-place change to a component
with a single declared replica is `potential_downtime`; stopping writes for an offline copy is
`known_downtime`; anything else not established is `unknown` — never read as online. Steps with known
or potential downtime are held against the request's constraints: unstated `downtimeAllowed` is
`missing_information`; `downtimeAllowed: false` is a `constraint_conflict` per step; when allowed, the
step traces the constraint and the maintenance window as stated (whether it fits is not modeled).

Compatibility questions (data model, schema, version, application, protocol, authentication, client
changes, rollback) are traced to their changes and stay `unknown`: a question is `verified` only with
machine-checkable evidence (an evidence trace), and no planning-time declaration is such evidence.

## Risk and assumption representation

A `Risk` has a stable id, a category (data loss, data inconsistency, downtime, capacity exhaustion,
compatibility failure, security regression, observability gap, rollback limitation, dependency
ordering, cost increase, operational complexity, irreversible change), a status — `confirmed` (the
evidence establishes it), `potential` (with its preconditions) or `unknown` (the evidence is
missing) — the elements and steps it affects, its impact, a mitigation or review action, and its
traces. There is no probability and no score. The request's assumptions are recorded on the plan as
stated and never computed with.

## Verification checkpoint behavior

A `Checkpoint` says what is verified, the expected condition, its status, its basis and whether it
blocks progression. Statuses: `pass`, `fail`, `warning` (only from a current modeled analysis),
`not_run` (no analysis yet, or observable only at runtime), `cannot_evaluate` (only stale analyses,
or no stated threshold), `manual_verification_required`. Bases: `modeled`, `manual`,
`runtime_observed`. Target validation (blocking), capacity, security and observability are
evaluated from the stored analyses of the target revision; data consistency, cutover and rollback
prerequisites and every manually verified step await a person; health checks and replication lag
are runtime observations. **A planning-time pass is never proof of runtime success.**

## Rollback and recovery modeling

Each high-impact step (cutover, decommissioning, write freeze, copy, replication, reconfiguration of
an existing component, any step whose reversibility is irreversible or unknown) has a
`RollbackConsideration`: triggers, the recovery action in words, what must be retained, its
preconditions, data-consistency implications, verification and limitations — distinguishing
reversible, conditionally reversible, irreversible and unknown. Switching clients back after the
target accepted writes is stated as divergence; an irreversible step says why; an unknown one says
it is not established. Every consideration is a proposal for review, never a command.

## Plan versioning and review lifecycle

A plan is a sequence of immutable versions. Statuses: `draft`, `needs_information` (a blocking
finding, or nothing to do), `ready_for_review`, `approved`, `rejected`, `superseded`, `archived` — and
no execution status. People move a version: submit (`migration.plan`), approve or reject
(`migration.approve`, naming the version number and the fingerprint of the content reviewed; a
rejection requires feedback), archive. Regenerating or revising appends a version and supersedes the
previous one, which keeps its content and review history; a version under review or approved is
replaced only with `replaceReviewed`; an identical result creates nothing. **Staleness is computed on
read**: a changed or unavailable source or target content, a later revision of the architecture, a
changed planner or pattern version, or a cited analysis no longer current. A stale version is shown
with its reasons and cannot be submitted or approved. Reading or revising never approves.

## Engine integration contracts

The other engines are read through their own stored analyses and the evolution contract's adapters
(`StoredAnalysis`) — never re-run, never recomputed. For each engine, the latest usable analysis of
the source revision and of the target revision is matched by revision number and content hash;
analyses of anything else are reported as stale and never used; failed analyses are ignored. Each
plan states its **coverage** per engine and side: current, stale, missing, or unsupported
(simulation compares scenarios of one revision, not a source and a target; a candidate's overlay is
analyzed by no engine, so the candidate's own evidence is cited). From current analyses:

| Engine | What the plan reads |
|---|---|
| Validation | the target's run: blocking, critical or high findings fail its checkpoint; others warn |
| Capacity | the target's scaling options and unscaled bottlenecks: checkpoint and capacity risks, with the engine's own figures |
| Cost | a cost increase, only when both sides were priced with the same snapshot and currency and both totals are complete — otherwise the comparison is withheld, with the reason |
| Reliability | violated requirements fail; lower modeled availability than the source warns |
| Security, observability | a target finding the source's analysis does not have fails its checkpoint and is a confirmed risk; never judged without the source's analysis |
| Simulation | unsupported for a source-to-target transition (stated in the coverage) |
| Evolution | a candidate target and its cited evidence; candidates are never regenerated or chosen |

A partial analysis never passes. A dimension without a current target analysis is a
`missing_evidence` finding (not blocking).

## API contracts

See [docs/api/migration-plans.md](../api/migration-plans.md) and the frontend contract
[docs/frontend/migration-contract.md](../frontend/migration-contract.md): fourteen endpoints under
`/api/v1/projects/{projectId}/migration-plans` — create, list, read the latest version, history, a
version, its steps and sequence, risks, checkpoints and rollback considerations, regenerate, submit,
approve, reject, archive. There is no execution endpoint.

## Example migration plan

A supported diff generates a plan. Source revision 1 declares `api` without a CPU limit; target
revision 2 sets `cpu_limit_cores: 2`. The plan (in-place, the default):

```json
{
  "status": "draft",
  "strategy": "in_place",
  "steps": [
    {"key": "configure:api", "type": "configure", "title": "Apply Api's target configuration",
     "completion": ["configuration.cpu_limit_cores is 2."], "dependsOn": [],
     "downtime": "unknown", "reversibility": "conditionally_reversible", "manualVerification": false},
    {"key": "verify:api", "type": "verify", "title": "Verify Api with its target configuration",
     "completion": ["api passes its verification with the target configuration."],
     "dependsOn": ["stp_4e50c0b4910e721c0227"], "downtime": "unknown", "reversibility": "reversible"}
  ],
  "sequence": [{"number": 1, "stepIds": ["stp_4e50c0b4910e721c0227"], "parallel": false},
               {"number": 2, "stepIds": ["stp_fc3276feee9451031e6d"], "parallel": false}],
  "checkpoints": ["validation:target (not_run, blocking)", "capacity:target (not_run)"]
}
```

The downtime is `unknown`: whether applying the configuration restarts `api` is not modeled, so it is
not claimed online.

## Example: missing information prevents a confident plan

Target revision 2 changes the database `db` from PostgreSQL to MySQL; the request states neither the
data to move nor whether downtime is allowed. The plan is generated, but its status is
`needs_information`:

```json
[
  {"type": "missing_information", "key": "data_scope:db",
   "missing": ["A data requirement for db: its scope, retention and verification."]},
  {"type": "missing_information", "key": "downtime_allowed",
   "missing": ["Whether downtime is allowed, and within which maintenance window."]}
]
```

## Example: a dependency cycle is rejected

Two steps that wait for each other:

```json
{"type": "dependency_cycle", "key": "a",
 "message": "These steps wait for each other, so none can be carried out first: a, b.",
 "missing": ["A dependency removed so that one of them can be carried out first."]}
```

The plan has no sequence and is `needs_information`; nothing is reordered to hide the cycle.

## Example: several strategies with their trade-offs

`api` declares 2 replicas and a health check behind a load balancer, and grows to 4 replicas. Three
strategies are supported; the plan follows in-place (the default) and lists every strategy:

| Strategy | Supported | Downtime | Trade-offs (excerpt) |
|---|---|---|---|
| `in_place@1` | yes | unknown | Simplest; downtime depends on each component and is not modeled. |
| `rolling@1` | yes | modeled online | One instance at a time; capacity reduced by one instance; old and new must be compatible. |
| `blue_green@1` | yes | modeled online | Online switch with a retained fallback; both environments run (and are billed); capacity for a second environment is not modeled. |
| `replication_cutover@1` | no (does not apply) | unknown | No stateful component is replaced. |
| `canary@1` | no | unknown | Traffic weights and the share of requests per version are not modeled. |

No strategy is scored, ranked or chosen by the engine.

## Example: a rollback limitation

The database changes technology in place, copied offline. Switching back after the cutover:

```json
{"reversibility": "conditionally_reversible",
 "action": "Switch the clients back to db's source instance.",
 "consistency": "Writes db's target instance accepts after the cutover are not in db's source instance: switching back makes the two diverge.",
 "limitations": ["Writes made to db's target instance after the cutover are lost to db's source instance unless reconciled."]}
```

Retiring the source instance is irreversible: `"limitations": ["Once removed, db and its data cannot
be restored by this plan."]`, with a confirmed `irreversible_change` risk and a potential
`data_inconsistency` risk (`"Clients are switched back to db's source instance after db's target
instance has accepted writes."`).

## Example: a plan requiring manual verification

The same offline database move requires a person's verification for `prepare:db:freeze`,
`backfill:db`, `verify:db:consistency`, `cutover:db`, `verify:db:traffic` and
`decommission:db:source`; each has a `manual_verification_required` checkpoint, along with
`consistency:db`, the cutover's prerequisites and the rollback prerequisites (the retained source
instance). The target's validation checkpoint stays `not_run` until a validation run of revision 2
exists.

## Example: a revision change makes a plan stale

A plan from revision 1 to revision 2 is created; then revision 3 is saved. Reading the plan:

```json
{"freshness": {"stale": true, "reasons": ["newer_revision"]}}
```

Submitting or approving it is refused with `409 stale_migration_plan` (details `{"reasons":
["newer_revision"]}`); regenerating creates a new version.

## Example: the canonical architecture is unchanged

Generating, regenerating, submitting and approving a plan read the revisions and write only the
plan's own versions and audit entries. The architecture's current revision and content are identical
before and after (verified by the integration tests), and the IR objects handed to the engine are
never mutated (verified for every fixture).

## Adding a deterministic migration pattern

1. Define its `PatternMeta` in `engines/migration/patternbook.py`: id, version 1, kind, the change
   kinds and aspects it applies to, evidence, preconditions, capabilities (from the component
   catalog's vocabulary), step types, dependency rules, risks, trade-offs, downtime and
   reversibility when its prerequisites hold, rollback, data requirements, validation, and what it
   does not support.
2. Implement `assess(context)`: read only what the source and target declare; return the elements
   it covers and, for every prerequisite not declared, what must be declared.
3. Register it in `default_registry()`; if it produces steps, add its step generation to
   `engines/migration/planner.py` with keys that make stable ids, explicit dependencies and
   completion criteria in words.
4. Add tests (applicability, missing prerequisites, steps, dependencies, determinism) and bump the
   version whenever its behavior changes: existing plans then read as stale (`models_changed`).

## Tests

```
make db-up          # Postgres, Redis and Mailpit for the integration and security suites
make lint typecheck test-unit test-integration test-security test-eval migrate-check
uv run pytest tests/unit/migration -q                         # the engine and domain
uv run pytest tests/integration/api/test_migration_plans.py  # the API against a real database
uv run pytest tests/security/test_traceability_migration_planning.py
```

`tests/unit/migration/test_migration_fixtures.py` holds the fifteen fixtures (vertical scaling,
database replacement, missing data volume, replication, monolith to services, blue-green, potential
downtime, unknown rollback, cycle, invalid references, staleness, several strategies, manual
verification, no mutation, rejected then revised) and checks every one for determinism, storage
round-trips and the absence of fabricated estimates.

## Known limitations

- **Not modeled, so never stated**: data volumes, throughput, durations, replication lag thresholds,
  schemas and data models, client libraries, credentials, traffic weights, how a component behaves
  when reconfigured (restart or not), capacity for a second environment. Each appears as missing,
  unknown or manual verification.
- Canary, expand-and-contract and strangler migrations are not planned; boundary changes and role
  changes are left to a person.
- A stateful replacement is recognized only for a same-id technology change or exactly one removed
  and one added component of the same kind; anything else needs a person to pair them.
- Compatibility is never `verified` at planning time: no engine produces machine-checkable
  compatibility evidence yet.
- Simulation results are not used: a source-to-target transition is not a supported scenario.
- A cited analysis is treated as immutable: it stays current for the plan once stored.
- A transition needing more than 500 steps (or 500 change findings) is refused as
  `too_many_changes`: split it into several migrations.
- Steps are never parallelizable today: no rule states that two steps may run together.
- The frontend still carries an older migration mock (`apps/web/api/migrations.ts`) with fields this
  API never returns; see the frontend contract.

## Repository audit

Before this milestone (2026-09-28), every migration file was an empty scaffold
(`engines/migration/{dependency_graph,planner,risk,rollback,sequencing,service}.py`,
`core/domain/migrations/{entities,plans,steps}.py`, `persistence/models/migration.py`); there was no
table, route, permission, ADR or test. Reused rather than duplicated: the IR diff, the evolution
candidate overlay, the evolution evidence adapters, the component catalog's capability vocabulary,
the engines' declared inputs, the permission matrix, the audit log and the project write lock.

## Final review

Plans are generated from exact stored revisions (or a candidate on the source revision) through the
IR diff; steps are traced, ordered and validated (references, cycles, prerequisites, manual
verification); data, downtime, compatibility, risks, checkpoints and rollback are stated without
fabricated values; the other engines' stored analyses are evidence with explicit coverage and
staleness; versions are append-only, reviewed on an exact fingerprint by authorized people, and
stale versions are refused; every operation is authorized and tenant-isolated; nothing executes.
The decision is recorded in [ADR-020](../adr/ADR-020-deterministic-migration-planning.md).
