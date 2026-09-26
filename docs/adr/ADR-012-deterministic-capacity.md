# ADR-012: Deterministic capacity: declared facts in the IR, explicit workloads, no implicit scaling

- Status: accepted
- Date: 2026-09-26

## Context

Milestone 7 adds capacity analysis. Every capacity, constraint, simulation and catalog file in the
repository was an empty placeholder. The Architecture IR described replicas, CPU, memory, storage
and connection limits, but nothing about throughput, how traffic is shared between connections, or
fan-out. There is no component catalog and no measurement of running systems, and the web app's
proposed contract assumed a single bottleneck and a "maximum supported daily active users".

## Decision

- **Declared facts live in the IR.** Throughput (a total, or per replica), CPU cost per unit of
  work, bandwidth, and on connections the traffic share, calls per request, cache hit ratio, access
  (read/write) and pool size are optional IR properties, with provenance like every other value.
  Adding them is backward compatible; the IR schema version does not change.
- **An explicit workload per analysis.** A typed profile (request/response, event stream, batch),
  stored with the analysis as a snapshot, with explicit units and named assumptions. No profile
  resource; it may cite the project's capacity requirements.
- **Models, not benchmarks.** Capacity comes from versioned models in code over declared values;
  every estimate names its source and basis; unknown is never zero. No catalog, no measurement: both
  are stated as limitations of every result.
- **No routing or scaling is assumed.** A connection carries demand only with a declared share or
  calls per request; capacity grows with replicas only where a per-replica throughput is declared
  (the linear assumption is then stated). Work in different units is never added up.
- **Candidates, not verdicts.** Bottlenecks are modeled only when demand and capacity are both
  known; unknown capacity on a waited path is a candidate. No single bottleneck and no maximum
  supported users are claimed; the saturation multiple says whether it is complete.
- **Scenarios are recalculations.** Growth and configuration changes rerun the same models on
  in-memory copies; this is not a simulation engine.
- **Synchronous, stored, lock-free calculation.** An analysis reads and authorizes, calculates with
  no transaction open, then stores (append-only) under a re-checked project lock, with the audit.

## Consequences

- Architects must declare what they know (every traffic-carrying connection needs a share); an
  architecture that declares little gets explicit "insufficient input" rather than numbers.
- Future engines (cost, reliability, simulation) read the same IR properties and the stored
  demand; a catalog, when it exists, becomes a new source of limits (`catalog`) without changing
  the contract.
- The web app's envelope and single bottleneck are not provided (docs/frontend/capacity-contract.md).
