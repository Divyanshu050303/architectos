# ADR-022: Coverage-aware drift detection between a revision and a stored discovery run

- Status: accepted
- Date: 2026-10-02

## Context

ArchitectOS holds versioned architectures, and the Discovery Engine reads what deployment artifacts
declare. People want to know where the two have diverged. The Phase 0 audit found no backend drift
code. The web app's proposed shape assumed a live connected source, a single latest report, a
severity per item and a matching/drifted verdict.

Drift detection is where it's easiest to overclaim. A component missing from a partial scan "was
removed". A parser upgrade "changed" every value. Two similarly named services "are the same". A
replica difference "breaks capacity". A difference "was unauthorized". Remediation would also mean
mutating the canonical architecture, or infrastructure, without a person deciding.

## Decision

### What is compared

- **Exact inputs, both stored.** The baseline is one revision chosen explicitly, recorded with its
  content hash and schema version. The observed state is a stored discovery run's immutable entities
  and relationships, not its review-dependent proposal. There's no live scanning and no scheduling.
- **Compatibility before comparison.** Eight dimensions are assessed first (`drift-compatibility@1`):
  IR schema, result version, extractor and rule versions, source types, source coverage, artifact
  coverage, identity and freshness.
  - Incompatible inputs produce no differences, only the reason why.
  - A source type whose extractor changed is `not_comparable`.
- **Identity, never names.** Nodes are matched by the stable discovery key, then by identities
  people confirmed (an append-only mapping history per architecture). A candidate in the same source
  location, or a key claimed twice, is `ambiguous` until a person decides. Connections are matched by
  relationship id or endpoints (`drift-identity@1`).
- **Typed comparison of what the source can declare.** Comparison reuses the IR diff, so values are
  canonical and secrets are redacted. It covers only the properties each source type can express
  (`drift-differences@1`). A value no longer declared is not a default.

### What is claimed

- **Classification says how far the evidence goes** (`drift-classification@1`). Findings are
  `confirmed`, `potential`, `not_comparable` or `unknown`.
  - A removal is `confirmed` only when the baseline element's source artifact (from its provenance)
    was read completely by the run. A partly read artifact makes it `potential`; an unread artifact or
    a hand-written element makes it `unknown`.
  - Every uninspected artifact is reported as `coverage_changed`.
  - There's no score or severity, only counts.
- **Impact is linked context** (`drift-impact@1`). A finding names the engines whose models read
  what it concerns: the catalog specification's per-property engine list, validation for structure,
  and capacity, reliability and security for connections. It links those engines' stored analyses of
  the baseline revision as `current`, `stale` or `missing`. Nothing is recomputed, and no requirement
  or decision is judged.

### How differences are followed and stored

- **Items carry the review.** Findings correlate across analyses into per-architecture drift items,
  by subject and property.
  - People acknowledge, investigate, accept, dismiss (with a reason), note, link (records of the same
    project) and resolve.
  - Resolving needs a later comparable analysis that inspected the item's sources and no longer
    detects it, or a revision link.
  - A difference detected again reopens its item.
  - History is never erased, and every action is audited. Accepting drift doesn't update the
    baseline.
- **Stored, append-only, read-only towards the architecture.**
  - Migration `0022` adds the three tables. Analyses and mappings are append-only. Items keep their
    identity and a growing history, and are never deleted.
  - A compared discovery run is kept.
  - A new `architecture.drift` permission (members and up) runs and reviews; viewers read.
  - Analyses are synchronous and rate-limited.
  - Drift code only reads architectures and discovery runs. A test enforces this.

## Consequences

- A drift result is reproducible and explainable: the same inputs, mappings, policy and rule versions
  give the same findings, ids, order and fingerprint, and each finding cites both sides.
- **Coverage bounds what can be said.** Supplying fewer artifacts gives fewer confirmed findings, not
  more removals. Hand-written baseline elements can never be confirmed removed, and rely on identity
  mappings to be matched at all.
- Upgrading an extractor or rule makes earlier baselines not comparable for that source type until a
  run with the new version is accepted. This is deliberate.
- **Nothing is remediated.** Changing the architecture after a review is an ordinary, explicit
  architecture edit, which can then be linked to the item.
- Discovery's lighter `baseline-comparison` endpoint remains, as an unstored, proposal-level view.
  Drift analysis is the stored, coverage-aware comparison with review.
- **Frontend.** The web app's connector-based, single-report shape needs alignment. See
  [docs/frontend/drift-contract.md](../frontend/drift-contract.md).
- **Out of scope:** continuous monitoring, alerting, runtime telemetry and authorization history. Any
  of them needs its own decision.
