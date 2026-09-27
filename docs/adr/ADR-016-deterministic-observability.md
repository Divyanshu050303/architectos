# ADR-016: Deterministic observability analysis: declared configuration, explicit coverage states, no attainment

- Status: accepted
- Date: 2026-09-27

## Context

Milestone 11 adds architecture-level observability analysis. Every observability file in the
repository (engine, agent, knowledge) was an empty placeholder. The Architecture IR had an
`observability` node kind and `retention_seconds`, but no way to say that a component emits logs,
metrics or traces, that a flow propagates the trace context, that something checks a health
endpoint, or that an alert can be delivered; criticality was not modeled either. The web app's
proposed contract assumed a 0–100 score, per-node booleans and SLOs with current attainment and
remaining error budget — values that only runtime telemetry can give. An observability tool that
reads a missing capability as present, or invents an SLO's current value, gives false assurance.

## Decision

- **Inputs are optional IR properties** (on components `criticality`, `logs`, `structured_logs`,
  `correlation_ids`, `metrics` kinds, `traces`, `trace_context`, `trace_sampling_ratio`,
  `health_check`, `alerts`, `owner`; on observability components `alert_delivery`; on connections
  `telemetry`, `trace_propagation`, `health_check`), with the IR's provenance. Nothing is defaulted
  and nothing is inferred from names or technologies: an undeclared property is **unknown**, never
  configured and never absent.
- **Criticality is declared**, never inferred from names, traffic or dependencies; an undeclared
  criticality makes checks about critical components not verifiable.
- **Coverage is an explicit state per component and dimension** (`modeled`, `partial`, `absent`,
  `unknown`, `unsupported`), counted in the summary; there is **no score, percentage or maturity
  level**. Collection is a modeled path of connections declaring the signal to an observability
  component.
- **Four kinds of finding, kept apart** (control gap, potential risk, violation, not evaluable), each
  type fixing its category and basis, enforced by the domain and the database; findings are ordered
  by severity, then basis.
- **The project's architecture policy is extended** with typed observability fields, snapshotted
  with each analysis; policy and requirements share one judge (with the security engine):
  satisfied only on declared evidence.
- **SLOs are traced, never evaluated**: availability, latency and throughput objectives map by a
  fixed table to the metric kinds that measure them; the checks say whether a collected indicator
  and a deliverable alert are modeled. Monitoring requirements map by a documented keyword table.
  Anything else is unsupported and never passed. No attainment, error budget or burn rate is
  computed.
- **No telemetry is read**: the engine imports no network or telemetry client; no request accepts
  measurements.
- **Analyzers in code, a generic orchestrator, synchronous stored analyses**, like the other engines.

## Consequences

- An architecture is analyzed only as far as it declares its observability: incompletely modeled
  ones get `unknown` coverage, `not_evaluable` findings and `not_verifiable` checks — modeling
  becomes visible work.
- A declared capability is taken as stated, not verified: whether telemetry flows, is retained and
  pages someone remains an operational question the documents and every result point to.
- The web app's score, booleans and SLO attainment are replaced by coverage states, findings and
  traceability checks (docs/frontend/observability-contract.md); showing live SLO status would need
  a separate, runtime-telemetry integration.
- Discovery (Terraform, Kubernetes, OpenTelemetry configuration) can later supply the same
  properties with their provenance without changing the engine or the contract.
