# ADR-014: Deterministic reliability: declared inputs, explicit paths, unknown never 0 or 1

- Status: accepted
- Date: 2026-09-26

## Context

Milestone 9 adds reliability analysis. Every reliability file in the repository (engine, rules,
agent, prompts, knowledge) was an empty placeholder, and no availability, MTBF, MTTR or SLA value
existed anywhere. Such values differ by provider, configuration and operation and cannot be
fabricated; typical figures would make estimates look authoritative while meaning nothing. The
Architecture IR already described replicas, zones, regions, replication, backups and connection
semantics (`kind`, `interaction`, `critical`). The web app's proposed contract assumed one
project-level estimated availability with a monthly downtime figure.

## Decision

- **Inputs are optional IR properties** (`availability`, `replica_availability`, `mtbf_seconds`,
  `mttr_seconds`, `min_healthy_replicas`, `failure_independence`, `failover_mode`,
  `failover_seconds`, `redundancy_group`, `redundancy_group_min_healthy`,
  `replication_lag_seconds`, `backup_interval_seconds`), with the IR's provenance. Nothing is
  defaulted and no catalog ships.
- **Redundancy is replicas plus redundancy groups.** Replicas combine by k of n and group members by
  the exact probability that at least k branches work, only when independence
  (`failure_independence: independent`) and automatic failover are declared per component.
- **Paths are explicit.** What a request requires is read from connection kind, interaction and
  `critical`; the analysis follows only those, and composes them in series.
- **Unknown is never 0 or 1.** An estimate without its inputs is `null` and names what it misses; a
  path is known only when all it requires is; objectives pass only on modeled values.
- **No score and no guaranteed uptime.** Results are estimates with their formula, inputs and
  assumptions; findings are modeled or candidate, with options for human review; there is no
  architecture-wide availability.
- **Models in code, a generic orchestrator** (as validation, capacity and cost): component models in
  precedence order, then architecture steps; failures are contained and reported.
- **Synchronous, stored, lock-free calculation**, like capacity and cost: read and authorize (with the
  in-force requirements), calculate on a worker thread with no transaction open, store append-only
  under a re-checked project lock.

## Consequences

- An architecture is estimated only as far as it declares reliability data; the results say
  precisely what is missing, and the findings (single points of failure, missing failover or
  recovery data) do not need numbers.
- Requirements with machine-checkable availability, RTO and RPO constraints are checked
  automatically; words-only requirements are listed as not verifiable.
- Measured data (monitoring, incidents) or provider catalogs could later supply declared values with
  their provenance without changing the engine or the contract.
- The web app's single project-level estimate is replaced by per-architecture analyses with per-path
  estimates (docs/frontend/reliability-contract.md).
