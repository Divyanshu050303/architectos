# Deterministic capacity engine

Milestone 7. The engine answers "can this architecture take this workload, under these stated
assumptions?" for one revision of the canonical [Architecture IR](architecture-ir.md). It
propagates an explicit workload through the architecture, compares demand with declared and
modeled capacity, and reports utilization, headroom, bottleneck candidates and growth scenarios.
It is deterministic and uses no language model. **Its results are model estimates from declared
inputs, not measurements**: it never claims that a system will support a production workload.
Decisions: [ADR-012](../adr/ADR-012-deterministic-capacity.md). API: [docs/api/capacity.md](../api/capacity.md).

## Responsibilities

In scope: workload demand per node and connection; throughput, CPU, connection, storage and
bandwidth estimates where a model and its inputs exist; utilization and headroom; bottleneck
candidates; the workload multiple at which the first known limit is reached; growth scenarios and
the scaling a model supports. Out of scope: cost, reliability, security, latency and queueing
simulation, a component catalog, measured performance, and feeding capacity into validation verdicts.

| Where | What |
|---|---|
| `core/domain/capacity/units.py` | Exact quantities and units |
| `core/domain/capacity/workload.py` | The workload profile |
| `core/domain/capacity/results.py` | The result contract: estimates, demand, utilization, bottlenecks, statuses, summary |
| `core/domain/capacity/analyses.py`, `scenarios.py` | The request, the analysis lifecycle and report, scenarios |
| `core/domain/capacity/capacity_service.py`, `ports.py` | Use cases; the engine port |
| `engines/capacity/engine.py`, `context.py` | The model contract, registry and orchestrator |
| `engines/capacity/traffic.py` | Demand propagation |
| `engines/capacity/{rps,compute,concurrency,storage,bandwidth}.py` | The models; `registry.py` lists them |
| `engines/capacity/headroom.py` | Utilization, headroom, bottlenecks |
| `engines/capacity/operating_envelope.py` | Scenarios and scaling options |
| `persistence/repositories/capacity.py` | Stored analyses (migration 0012) |

## Workload profile

Typed, per workload type; only the fields a type needs, nothing filled in by default.

| Type | Required | Optional | Not allowed |
|---|---|---|---|
| `request_response` | `peak_rate` (request rate) | `average_rate` (≤ peak), `peak_duration`, `concurrent_users`, `concurrent_connections`, `read_ratio`, `request_payload`, `response_payload` | batch fields |
| `event_stream` | `peak_rate` (event rate) | `average_rate`, `peak_duration`, `request_payload` (message size) | `read_ratio`, `response_payload`, `concurrent_users`, batch fields |
| `batch` | `batch_size` (records), `batch_interval` | `request_payload` (record size) | rates, peak duration, concurrency, response size |

Common and optional: `growth` (a multiplier offered to scenarios), `target_utilization` in (0, 1],
named `assumptions` (key, statement, optional quantity), and `requirement_ids` citing the project's
capacity requirements. The design rate is the peak rate, or a batch's records per second (size ÷
interval). The profile is stored with each analysis as a snapshot.

## Supported units

Exact decimals (never floats): non-negative, finite, at most 9 decimal places, below 10^15. Each
unit belongs to one dimension; different dimensions are never compared or combined. Canonical
units in **bold**.

| Dimension | Units |
|---|---|
| request rate | **requests/second**, requests/minute, requests/hour, requests/day |
| operation rate | **operations/second**, operations/minute |
| event rate | **events/second**, events/minute, events/hour |
| data rate | **B/s**, KB/s, MB/s, GB/s (decimal) |
| data size | **B**, KB, MB, GB, TB (decimal) |
| duration | **ms**, s, min, h, d |
| connections, users, cores (millicores), replicas, ratio (%) | **connections**, **users**, **cores**, **replicas**, **ratio** |

Rate, size and duration symbols and factors match the requirements' unit table. Calculated values
are rounded half-even at the 9th decimal place only when they become quantities; ratios are exact
to 9 places; presentation rounding is left to the reader.

## Capacity model interface

A model declares `ModelMeta`: a stable `id` and `version`, the node `kinds` it supports, the
`resources` it estimates, the `configuration` properties, `workload` fields and `assumptions` it
requires, typed `parameters`, and its `limitations`. `estimate(inputs)` receives the node, the
demand that reached it (and whether that demand is complete), the read-only context and its
parameters, and returns limits (capacity) and resource amounts (requirements), each an `Estimate`
with its source, basis and inputs. The orchestrator checks the output (one node, the model's own
id, unknown values naming what they miss).

The **registry** is built in code (no dynamic imports, no user code), refuses duplicate ids,
resolves by id and kind, validates selections and parameters, and versions the model set (a hash
of every model's id and version).

## Registered models

All version 1. None comes from a catalog or a benchmark; each uses what the architecture declares.

| Model | Kinds | Requires | Formula |
|---|---|---|---|
| `declared-throughput` | every deployed kind | `throughput_limit_per_second` | limit `work_rate` = the declared total |
| `replica-throughput` | services, workers, gateways, balancers, databases, caches, queues, observability | `throughput_per_replica_per_second`, `replicas` | limit `work_rate` = per replica × replicas (**linear in replicas: this model's stated assumption**) |
| `cpu-demand` | services, workers, gateways | `cpu_core_seconds_per_request` | demand `cpu` = work/s × core-seconds; limit `cpu` = replicas × `cpu_limit_cores` |
| `connection-pool` | databases, caches, gateways, balancers | `max_connections` | demand `connections` = Σ per source of the largest `pool_size` × source `replicas` (pools assumed full); limit = `max_connections` |
| `storage-growth` | databases, object stores, queues | `storage_bytes`, workload `request_payload` | `storage_growth` = writes/s × payload (× `replication_factor` for queues); with `retention_seconds`: `storage` = growth × retention; without: `time_to_full` = `storage_bytes` ÷ growth, **from empty** |
| `network-bandwidth` | every deployed kind | workload `request_payload` | demand `bandwidth` = work × request size + requests × response size; limit = `network_bandwidth_bytes_per_second` |

Planned, not implemented: memory (what a request holds depends on concurrency and runtime),
catalog-based limits, measured capacity.

## Input assumptions and IR properties

Capacity facts live in the architecture as optional IR properties (backward compatible), each with
provenance: on nodes `throughput_limit_per_second`, `throughput_per_replica_per_second`,
`cpu_core_seconds_per_request`, `network_bandwidth_bytes_per_second` (plus the existing
`replicas`, `cpu_limit_cores`, `max_connections`, `storage_bytes`, `retention_seconds`,
`replication_factor`); on connections `traffic_ratio`, `calls_per_request`, `cache_hit_ratio`,
`access`, `pool_size`. The workload and the request carry named assumptions; the analysis stores
them, and bottleneck candidates list the keys they rest on.

## Demand propagation

1. The workload arrives at its **entries**: the nodes the request names, else the clients. Each
   emits the design rate as units of work per second: requests, events (event stream) or
   operations (batch). Entry shares above the whole workload are reported.
2. Demand follows `request`, `data_access` and `publish` connections from source to target, and
   `consume` connections from the broker to the consumer; `replication` and `dependency` carry none.
3. A connection carries `work × traffic_ratio × calls_per_request`, then `× (1 − cache_hit_ratio)`,
   then `× read_ratio` or `× (1 − read_ratio)` when `access` is `read` or `write`. At least one of
   `traffic_ratio` and `calls_per_request` must be declared (the other counts as 1): **there is no
   100 % default**.
4. Nodes are processed in topological order (ties by id). A node's work is the sum of its inbound
   demand, in one unit.

Never guessed, always reported (`unsupported`): undeclared routing (`routing_unspecified`), a
source outside the workload (`unmodeled_source`), traffic cycles (`cyclic_traffic`), work arriving
in different units (`mixed_work_units`), demand beyond 10^15 per second (`demand_overflow`), no
entry (`no_entry`). Nodes after any of these have **incomplete** demand (`demand_incomplete`): a
lower bound, never used as a total. Every demand value keeps its hop (upstream node, connection,
node) and factors. Propagation is linear in the workload rate.

## Evidence, provenance and unknowns

Every estimate has a **source**: `declared` (a property the architecture states), `model_estimate`
(a named, versioned model's formula), `assumed` (a workload assumption), `unknown`; `measured` and
`catalog` exist in the contract and are never produced today. An unknown value has **no number**
(never 0) and names what it misses (`configuration.replicas`, `workload.read_ratio`, `demand`,
`magnitude`, …). A component is `estimated` (some model produced a known value),
`insufficient_input` (models apply, inputs are missing) or `unsupported` (no model applies); the
analysis status summarizes them (`completed`, `partial`, `insufficient_input`, `unsupported`), or
is `failed` when the engine could not run. Every result states its limitations
(`catalog_unavailable`, `no_measurements`, `no_components`).

## Bottleneck analysis

Per node and resource, demand is compared with capacity in one unit. **Utilization** =
demand ÷ capacity, exact, never clamped (1.5 = 50 % over). **Headroom** = capacity − demand
(negative when exceeded); **relative headroom** = 1 − utilization; **headroom to target** =
target × capacity − demand. Zero capacity with demand is `no_capacity` (no division); zero demand
is `idle`; an unknown side is `unknown`. When several throughput limits are known, the lowest binds.

Bottlenecks: `exceeds_capacity`, `at_capacity`, `above_target`, `no_capacity` are **modeled** (both
sides known); `unknown_capacity` on a node the workload reaches over a connection its callers wait
for (synchronous, not declared non-critical) is a **candidate**. None is called *the* bottleneck.
The summary's `saturation_multiple` is the smallest capacity ÷ demand over resources that grow with
the workload; `saturation_complete` is true only when every component the workload reaches has a
known throughput capacity and no demand is incomplete.

## Scenarios

A scenario changes the workload explicitly (a `growth` multiplier, `growth_rate` compounded over
`periods`, or a `target_rate`; optionally the target utilization) and may change capacity and
traffic properties of named nodes and connections. It runs the same models on in-memory copies
(the changed IR must stay valid): deterministic recalculation, not simulation, with no time
dimension. Nothing scales implicitly: a declared total stays as declared. **Scaling options** exist
only where a model defines scaling: replicas for `replica-throughput` (ceil(demand ÷ (per replica ×
goal))), CPU per replica or replicas for `cpu-demand`; otherwise `scaling_unsupported`. The
comparison lists changed inputs and configuration, per-resource demand, capacity and utilization
before and after, and new and resolved bottlenecks.

## API contracts

See [docs/api/capacity.md](../api/capacity.md): run (with up to 10 scenarios), list, read, components,
bottlenecks, scenarios, and the model catalog.

## Persistence

`capacity_analyses` (inputs, model set, fingerprints, summary, connection demand, unsupported,
limitations, scaling, scenario outcomes, error), `capacity_components` and `capacity_bottlenecks`
(rows, for paging and filtering). Append-only (triggers); same-project foreign keys to the
architecture, the revision and the analysis. The analysis runs in three steps: read and authorize
(short transaction), calculate (no transaction, no lock: revisions are immutable), store (re-checked
under the project lock, with the audit entry).

## Authorization

Running needs `architecture.analyze` (members and up) and a modifiable project and architecture;
reading needs `architecture.read`. Lookups go project → architecture → analysis; rows are read by
project and analysis; cited requirements must belong to the project. Organization, project and
actor come from the path and the session; unknown body fields are refused.

## Determinism

For the same revision content, workload, request (models, parameters, assumptions, entries) and
model versions, the result and its `fingerprint` are equal: models run in id order on an immutable
context, propagation in topological order with ties by id, every collection is sorted, arithmetic
is exact decimal, no clock or random value enters a result.

## Limits and performance

| Limit | Value |
|---|---|
| Architecture | 1,000 nodes, 5,000 connections (IR) |
| Scenarios per analysis | 10 (unique names), 50 changes each, 120 periods |
| Models selected, parameters, assumptions, entries | 50 each |
| Components page | 500 |
| Analyses | 120 per user per hour |

Measured on the largest architecture the IR allows: the engine 0.37 s per run (5.3 s with 10
scenarios); through the API with storage 0.6 s (2.8 s with 10 scenarios; 7 MB response); a
500-component page 0.2–0.3 s; read cost independent of the number of scenarios. Work is linear in
nodes and connections; no caching.

## Security

No code or expressions from requests (models are code in this package), no dynamic imports; every
input bounded; results carry node and connection ids and computed values, never configuration
`extra`, metadata or descriptions; errors carry fixed messages; audit entries carry ids and counts.

## Known limitations

- No catalog and no measurements: capacities are what the architecture declares.
- Linear scaling only where a per-replica figure is declared; no contention, coordination or
  queueing effects; no latency.
- Every connection carrying traffic needs a declared share or calls per request.
- A node receiving several kinds of work (requests and events) has unknown demand.
- Connection pools are counted full; storage fill time from empty; memory not modeled.
- Responses with many scenarios on the largest architectures are large.

## Adding a model

1. Write a class with `meta = ModelMeta(...)` (new id, version 1, kinds, resources, required
   configuration, workload fields and assumptions, limitations) and `estimate(inputs)`, building
   estimates with `engines/capacity/estimates.py`; unknown values name what they miss.
2. List it in its module's `MODELS` and, for a new module, in `registry.py`.
3. A change to what a model computes is a new `version`.
4. Test the known case, each missing input, zero and huge values, and determinism.

## Tests

```
make test-unit          # tests/unit/capacity
make test-integration   # tests/integration/api/test_capacity.py
make test-security      # sweeps, documentation, traceability (test_traceability_capacity_engine.py)
make migrate-check      # migration 0012
```

## Repository audit

Before any change (phase 0): `engines/capacity/*` (11 files), `engines/constraints/*`,
`engines/simulation/*`, `core/domain/components/*`, `knowledge/*.yaml`,
`persistence/models/{simulation,component}.py` and `apps/api/schemas/simulation.py` existed but were
empty: no capacity calculation, workload model, catalog or capacity persistence existed. Reused: the
IR and its `Topology`, its configuration properties and provenance, the requirements' unit
conventions and capacity metrics, the validation engine's patterns (registry, orchestrator, engine
port, stored immutable runs, sweeps), project access and locking, audit, rate limits, pagination.
Placeholders were filled rather than parallel files created.

## Final review

Two independent reviews (security, correctness). Security: no critical or high issue; a scaling
option too large to state now becomes `scaling_unsupported`. Correctness: two bugs, fixed — work in
different units was summed into one figure (now `mixed_work_units`, incomplete), and fan-out beyond
10^15 per second aborted the analysis (now `demand_overflow`, incomplete downstream). Confirmed
sound: tenant scoping, error mapping, determinism, bounded growth arithmetic.
