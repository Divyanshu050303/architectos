# ADR-018: Deterministic evolution: evidence-backed configuration proposals, decided by people

- Status: accepted
- Date: 2026-09-27

## Context

Milestone 13 adds architecture evolution: how a revision could evolve toward goals such as a higher
workload, an availability objective or a finding to address. Every evolution, decisions and
migration-planning file in the repository was an empty placeholder. The Capacity, Cost,
Reliability, Security, Observability and Validation engines already stored deterministic analyses
of each revision, and the Simulation Engine already evaluated configuration changes on an immutable
overlay next to the unchanged baseline.

The web app proposed a roadmap of stages (V1, V2, …) with daily active users, a monthly cost, a risk
level and "max supported users", and free-text ADRs. Most of these can only come from an invented
growth model or a judgment presented as a calculation. An engine that fabricates them, ranks
architectures, or edits the architecture of record gives false confidence and removes the human
decision.

## Decision

- **Candidates are configuration changes of existing elements only.** Six versioned rules
  (`scale-replicas`, `scale-cpu`, `add-replica`, `require-tls`, `encrypt-at-rest`,
  `enable-signal`) propose the values the evidence states. Structural changes (new components,
  caches, queues, splits, sharding, regions) are never proposed: no model evaluates them, so they
  are reported as `structural_consideration` findings for human review.
- **Evidence is the other engines' stored analyses plus typed goals.** An analysis counts only when
  it ran on the revision's exact content (same content hash); otherwise it is `stale`, reported and
  never used. Missing evidence is requested, never assumed. Triggers carry no urgency.
- **Impacts come from the existing engines.** The Simulation Engine evaluates the candidate's overlay
  next to the baseline for capacity, reliability and cost; the Security and Observability engines
  analyze both sides. The Validation Engine validates both sides with its own rules. The evolution
  engine adds no formula. A dimension an engine cannot establish stays `unknown` or `unsupported`.
- **Trade-offs are a table, never a score.** Each row states its direction and basis: `modeled`,
  `rule`, or `consideration` for human review. Alternatives for a goal are shown side by side in
  canonical order; there is no rank, weight or winner.
- **People decide, in a minimal decisions domain.** A decision record (ADR-n per project) is drafted
  from an analysis with its candidates as options; a person accepts one or rejects them all with a
  rationale; an accepted decision may be superseded, and a person may link the revision that applied
  it. Accepting changes nothing in the architecture: applying a change is a separate, authorized
  architecture workflow.
- **Execution is synchronous, bounded and stored.** At most 25 candidates are validated and
  evaluated per analysis, with the cut stated. Analyses and candidates are stored append-only
  (migration 0019); decisions change only through their lifecycle. Audited as
  `architecture.evolution_analyzed` and `decision.*`.

## Consequences

- **Evolution candidates are proposals requiring engineering review and authorization.** `valid`
  means valid under the modeled constraints, not production-ready; impacts are model-based
  projections, not guarantees of production behavior.
- An architecture that declares little gets few candidates and many `missing_evidence` findings:
  modeling and running the engines becomes visible work, not a silent default.
- Adding a kind of evolution means adding a rule whose trigger an engine already states; changing a
  rule's behavior bumps its version and changes its candidate ids.
- Structural evolution, cost reduction, failover and zone placement need models of their own before
  any rule can propose them. Migration planning is a later milestone.
- The web app's roadmap stages, users, risk levels and free-text ADRs are replaced by analyses,
  candidates, alternatives and decision records (docs/frontend/evolution-contract.md).
