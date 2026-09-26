# Reliability engine

Deterministic, explainable reliability analysis of an architecture revision: what each request
path needs, its availability where the architecture makes it calculable, single points of failure
and other risks for review, and reliability objectives checked against modeled evidence.

Estimates are **not guaranteed uptime and not measurements**: every number is a declared value or
comes from a declared value through a stated model, and what cannot be established is unknown —
never 0, never 1. No language model takes part; the same inputs always give the same result and
`resultFingerprint`.

Code: `core/domain/reliability/` (values, inputs, results, request and lifecycle, report, service),
`engines/reliability/` (orchestrator, component models, request paths, composition, findings,
objectives), `persistence/` (migration 0015), `apps/api/routes/reliability.py`. API:
[docs/api/reliability.md](../api/reliability.md). Decision:
[ADR-014](../adr/ADR-014-deterministic-reliability.md).

## Purpose and scope

In scope: component availability, recovery time and data-loss window from declared inputs; request
paths from explicit connection semantics; series and declared-alternative composition; single
points of failure, redundancy, failure domains, failover and recovery findings; objectives from the
request and from the project's requirements, with their verdicts; stored, append-only analyses.

Out of scope: production monitoring, incidents or alerts, SLO dashboards from telemetry, chaos
experiments, traffic simulation, changing the architecture, provider SLA scraping, default or
typical MTBF/MTTR values, language-model conclusions.

## Input fields and units

Component inputs are optional IR node properties (`core/architecture_ir/configuration.py`), with
the IR's per-field provenance; nothing is defaulted:

| Property | Unit | Meaning |
|---|---|---|
| `availability` | fraction in [0, 1] | the component as a whole (e.g. a provider's commitment) |
| `replica_availability` | fraction | one replica |
| `mtbf_seconds`, `mttr_seconds` | seconds | one replica's mean time between failures and to repair |
| `replicas` (existing), `min_healthy_replicas` | count | k of n replicas needed |
| `failure_independence` | `independent`, `correlated`, `unknown` | whether replicas (and group members) fail independently |
| `failover_mode`, `failover_seconds` | `none`/`manual`/`automatic`; seconds | how and how fast work moves off a failure |
| `redundancy_group`, `redundancy_group_min_healthy` | identifier; count | interchangeable components, and how many must serve |
| `replication_mode` (existing), `replication_lag_seconds` | seconds | what asynchronous replication can lose |
| `backup_enabled` (existing), `backup_interval_seconds` | seconds | what a restore can lose |
| `region`, `availability_zones`, `multi_az` (existing) | | failure domains |

Connections: `kind`, `interaction` and `critical` (existing) decide what a request requires.
Availability is kept to 9 decimal places (a `ratio` quantity; 10^-9 of a 730-hour month is 2.6 ms);
durations are quantities with units (`ms`, `s`, `min`, `h`, `d`). Objectives in the request use the
same units. The request carries only entries, objectives and assumptions.

## Supported models and formulas

Component models, in precedence order (the first to establish a value stands; later ones may use
earlier estimates):

| Model | Estimates | Formula / rule |
|---|---|---|
| `declared-availability` | `availability` | the declared value |
| `declared-replica-availability` | `replica_availability` | the declared value |
| `mtbf-mttr` | `replica_availability` | `MTBF / (MTBF + MTTR)`, steady state; both 0: undefined |
| `replicas` | `availability` | one replica: `a`; n replicas, k needed: `Σ_{i=k..n} C(n,i) a^i (1−a)^(n−i)`, only with declared `min_healthy_replicas`, `failure_independence: independent`, `failover_mode: automatic`; at most 1,000 replicas |
| `recovery-time` | `recovery_time` | `failover_seconds` when failover is manual or automatic, else `mttr_seconds` |
| `data-loss-window` | `data_loss_window` | the least of synchronous replication (0), `replication_lag_seconds`, `backup_interval_seconds` (backups not disabled) |

Architecture steps, in order: `request-paths` (paths and their composition), `path-findings`
(paths not evaluable, unverified data), `topology-findings` (single points of failure,
redundancy, failure domains, failover, recovery, cycles), `objectives`.

**Request paths.** From each entry (the request's `entries`, else every client), required
connections are followed breadth first in id order, each node once: `request` and `data_access`
unless `asynchronous`, and `dependency`, unless `critical: false`. Asynchronous requests,
`publish`, `consume` and `critical: false` are recorded as optional; `replication` is not a request
dependency. An unstated interaction is treated as waiting and reported.

**Composition.** Series: the product of every required component's availability. Declared
alternatives: members of one redundancy group on a path, each with its branch (the member and what
only it requires), give `P(at least k of n branches)` exactly, only when every member declares
independent failures and automatic failover and they agree on `k`; what every branch needs stays
in series. Overlapping or nested alternatives, and disagreeing members, leave the path unknown.

**Objectives.** `availability` (at least a fraction), `recovery_time` and `data_loss` (at most a
duration), `redundancy` (at least a count), optionally strict. Checked on the named components, else
on every path (availability) or every relevant component. In-force `availability` and
`reliability` requirements become objectives when machine-checkable: availability or uptime floors,
`rto` and `rpo` ceilings, on the components referencing them, else their scope's, else the paths.

## Assumptions and limitations

- The series product and k-of-n assume failures of different components, and of declared
  independent replicas, are independent: stated with every estimate.
- A declared failover is taken to work and to be instant for availability (declared, not verified;
  failover time is not deducted); recovery time uses the declared failover time.
- MTBF/MTTR give long-run averages, not the length or timing of any outage.
- Not modeled: failure rates (no conversion between rate, MTBF and availability), partial
  degradation, load-dependent failure, detection time, maintenance windows, provider SLAs that are
  not declared, data durability, requirements stated only in words.
- There is no architecture-wide availability: it would need each entry's share of requests.
- Stale data is not judged (it needs a reference date; the analysis reads no clock).

## Unknown and unsupported behavior

- An estimate without its inputs has no value and names what is missing
  (`configuration.mttr_seconds`, `db.availability`, `eu.failure_independence`,
  `automatic.failover_mode`, `consistent.min_healthy_replicas`, `magnitude`, …).
- A path is known only when every required component's availability is; otherwise it lists what
  it lacks and an `availability_not_evaluable` finding names the components.
- A component no model applies to is unsupported (`no_reliability_model`); a model or step that
  fails is reported (`model_failed`, `step_failed`, `invalid_output`) and everything else still
  counts. No entry at all is `no_entry`.
- An objective is `satisfied` or `violated` only by modeled values; any unknown value makes it
  `not_verifiable`, never a pass; `not_applicable` when nothing is concerned.
- Status: `completed`, `partial`, `insufficient_input`, `unsupported`, `failed`.

## Example analysis

Architecture: `web` (client) → `api` (3 replicas, 2 needed, `replica_availability` 0.99,
independent, automatic failover in 30 s, zones a, b, c) → `db` (1 replica, MTBF 99 s, MTTR 1 s,
backups every hour). Request:

```json
{"objectives": [
  {"key": "slo", "kind": "availability", "target": "0.98"},
  {"key": "rto", "kind": "recovery_time", "duration": {"value": 5, "unit": "min"}}
]}
```

Result (trimmed):

```json
{
  "status": "completed",
  "paths": [{
    "entryId": "web", "nodeIds": ["web", "api", "db"], "connectionIds": ["api-db", "web-api"],
    "complete": true,
    "availability": {"quantity": {"value": "0.98970498", "unit": "ratio"}, "source": "model_estimate",
                     "inputs": [{"label": "assumption", "value": "Failures of different components are independent …"},
                                {"label": "api.availability", "value": "0.999702"},
                                {"label": "db.availability", "value": "0.99"}]}
  }],
  "objectives": [
    {"key": "rto", "target": "<= 5 min", "verdict": "satisfied",
     "actual": [{"label": "api.recovery_time", "value": "30"}, {"label": "db.recovery_time", "value": "1"}]},
    {"key": "slo", "target": ">= 0.98", "verdict": "satisfied", "actual": [{"label": "web.availability", "value": "0.98970498"}]}
  ],
  "summary": {"paths": 1, "pathsEstimated": 1, "findings": {"high": 1, "…": 0}}
}
```

`api` is 3 × 0.99² × 0.01 + 0.99³ = 0.999702; `db` is 99 / (99 + 1) = 0.99; the path is their
product. Findings (GET …/findings) include `single_point_of_failure` for `db` (high, modeled: one
replica, every request path needs it, `affects: api, web`).

## Example: a partial result

The same architecture without `db`'s MTBF and MTTR:

```json
{
  "status": "partial",
  "paths": [{"entryId": "web", "complete": false,
             "availability": {"quantity": null, "missing": ["db.availability"], "source": "unknown"}}],
  "objectives": [{"key": "slo", "verdict": "not_verifiable", "missing": ["db.availability"],
                  "explanation": "Not established for web: their values are not modeled."}]
}
```

`api` is still estimated (its component result says so); the path, and the objective, are not:
nothing is guessed for `db`. Findings: `availability_not_evaluable` (naming `db` and what it lacks),
`objective_not_evaluable` for `slo`, `missing_recovery_data` for `db`, and `db`'s single point of
failure.

## Interpreting findings

Each finding says what was detected, which elements (`nodeIds`, `connectionIds`), the evidence
(declared properties, the paths that need it, what its failure affects), why it matters, what is
missing, and options for human review. `modeled`: the declared facts establish it; `candidate`:
they are incomplete, worth a look. Severity follows validation's scale (critical to info);
objective violations take the requirement's priority. Ids are stable (type, elements, objective):
the same finding keeps its id across analyses. There is no risk score, and no finding says an
outage will happen; recommendations are never applied automatically.

## API contracts

See [docs/api/reliability.md](../api/reliability.md): run, list, read, components, findings, and
the model catalog.

## Persistence

`reliability_analyses` (inputs with the requirements read, model set, fingerprints, summary, paths,
objective verdicts, unsupported, limitations, error), `reliability_components` and
`reliability_findings` (at their canonical position, with their stable id). Append-only (triggers);
same-project foreign keys to the architecture, the revision and the analysis. An analysis reads and
authorizes (with the in-force requirements), calculates on a worker thread with no transaction
open, then stores under a re-checked project lock with its audit entry.

## Authorization

Running needs `architecture.analyze` (members and up) on a modifiable project and architecture;
reading needs `architecture.read`. Lookups go project → architecture → analysis; organization,
project and actor come from the path and the session, never the body; unknown body fields are
refused. The audit entry (`architecture.reliability_analyzed`) carries ids and counts only.

## Determinism

Nodes in id order, models in precedence order, steps in order, traversal breadth first in
connection id order, findings deduplicated and ordered by severity, type and id, every collection
sorted, exact decimals, no clock or randomness. The context fingerprint covers the revision
(content hash), the request, and the requirements read (id, version, status). Reordering nodes or
connections changes nothing.

## Limits and performance

| Limit | Value |
|---|---|
| Architecture | 1,000 nodes, 5,000 connections (IR) |
| Entries, objectives, assumptions | 50 each; objective scope 200 nodes |
| Replicas combined by k-of-n | 1,000 |
| Components and findings page | 500 |
| Analyses | 120 per user per hour |

Measured on the largest shapes the IR allows: a 999-component chain 0.3 s, a 998-component cycle
0.5 s, 500 entries into a 499-component chain 0.85 s (7.9 MB result), a 998-member redundancy group
1.0 s, 500 entries reaching a 499-member group 0.8 s (22.8 s before per-analysis memoization).
Through the API with storage, a 999-component chain with 50 objectives: 0.47 s, 20 SQL statements;
reads 0.02–0.07 s. Traversal is bounded by the IR's size; closures and group compositions are
computed once per analysis.

Risks: analyses are synchronous (on a worker thread shared with the capacity and cost engines; no
per-analysis time budget beyond the rate limit); results grow with entries × path length.

## Security

No code or expressions from requests, no dynamic imports; every input bounded; requirement text is
never interpreted (only structured constraints); results carry element ids, declared values and
computed ones; texts name at most 20 elements and evidence lines 100 (the lists carry them all);
errors carry fixed messages. Sweeps cover every endpoint.

## Adding a model

1. Component model: a class with `meta = ModelMeta(...)` (new id, version 1, kinds, resources among
   `availability`, `replica_availability`, `recovery_time`, `data_loss_window`, required
   configuration, assumptions, formula, unsupported conditions, limitations) and
   `estimate(inputs) -> ModelOutput`; unknown estimates name what they miss; register it in
   `engines/reliability/registry.py` where its precedence belongs.
2. Architecture step: a class with `meta = StepMeta(...)` declaring what it produces (`paths`,
   `findings`, `objectives`) and `run(context, progress)`; never mutate the context.
3. A change to what a model computes is a new `version`. Never add a default or typical value.
4. Test the known case, each missing or invalid input, the edges (0, 1, huge), and determinism.

## Tests

```
make test-unit          # tests/unit/reliability
make test-integration   # tests/integration/api/test_reliability.py, migrations
make test-security      # sweeps, documentation, traceability (test_traceability_reliability_engine.py)
make migrate-check      # migration 0015
```

## Repository audit

Before any change (phase 0): `engines/reliability/*` (8 files), `engines/simulation/*`,
`engines/constraints/*`, `core/domain/components/*`, `engines/validation/rules/{reliability,
availability}.py`, `ai/agents/reliability_agent.py`, `ai/prompts/reliability/` and every
`knowledge/*.yaml` were empty; no reliability data existed anywhere. Reused: the IR (connection
`critical`, `interaction` and kinds; replicas, zones, regions, replication, backups; `Topology`;
per-field provenance), capacity's `Estimate`, `Quantity` (ratio and durations), value sources and
modeled/candidate certainty, validation's severities, verdicts, requirement scopes and priority
severities, the requirements' availability/RTO/RPO metrics, the engine pattern of validation,
capacity and cost (registry, orchestrator, port, stored append-only analyses, three-step service,
sweeps). The exact decimal context moved to `core/domain/numbers.py`, shared with cost.

## Final review

Two independent reviews (security, correctness). Security: one high finding, fixed — 500 entries
reaching one large redundancy group recomputed the same traversals and group composition per entry
(22.8 s; now memoized per analysis, 0.8 s); a medium one, fixed — result lists capped at 200 dropped
whole steps on large architectures (now bounded by the architecture's size, with texts shortened).
Correctness: k-of-n raised on 0^0 for perfectly available replicas (fixed); dotted node ids were
misattributed in path findings (fixed). Confirmed sound: tenant scoping, authorization, input
bounds, decimal edges, requirement handling, failure isolation, formulas, composition, verdicts,
persistence.
