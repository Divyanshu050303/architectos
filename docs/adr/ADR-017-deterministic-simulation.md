# ADR-017: Deterministic simulation: an immutable overlay evaluated by the existing engines

- Status: accepted
- Date: 2026-09-27

## Context

Milestone 12 adds scenario simulation. Every simulation file in the repository was an empty
placeholder: the engine, its scenario modules, the domain, persistence, schema, worker and script.
There was no route, table or ADR. The Capacity, Reliability and Cost Engines already had deterministic
models:
- capacity: scenarios with a workload multiplier and configuration changes;
- reliability: dependency closure, roles, redundancy groups and failover;
- cost: scenarios priced with one snapshot and compared.

The Architecture IR had pure, all-or-nothing edit commands with provenance.

The web app proposed a queued job with traffic presets (2x, 10x), a duration, an environment, a
timeline in seconds, before-and-after error rates, a "cascading failure likely" verdict and an
impact level. Most of these can only come from runtime measurement or an invented model. A simulator
that fabricates them gives false confidence, and one that edits the architecture corrupts the design
of record.

## Decision

- **Scenarios are typed and versioned.** There are nine types:
  - workload change;
  - replica, traffic, resilience and resource configuration changes;
  - component, connection, zone and region failures.

  Each type declares its inputs and units, elements, overlay semantics, analyses, requirements,
  unsupported conditions and limit. A change to a property no simulated engine reads is refused.
  Validation runs before any engine does, and names the field and element.
- **The overlay is immutable.** Configuration changes are IR edit commands applied to an in-memory
  copy, with the provenance `scenario:<fingerprint>`. Failures mark elements unavailable instead of
  removing them. The overlay is serialized without the architecture and can be reconstructed from
  the stored revision. The stored revision is never modified.
- **The existing engines evaluate; the simulation engine adds no formula.** Evaluators call:
  - the Capacity Engine's scenario;
  - the Reliability Engine's semantics, with impacts interrupted, degraded, tolerated, unknown or
    unaffected;
  - the Cost Engine, with one snapshot and its own comparison.

  They run in a generic orchestrator with one evaluator per analysis. An analysis whose inputs are
  missing is unsupported, with the reason, never estimated.
- **Baseline and scenario are evaluated in one run**, with the same models, snapshot and settings.
  Deltas carry units, exact decimals, and a difference or percentage only where defined. Values that
  cannot be compared are marked with a note, and no cause is inferred. Stored simulations are
  compared per analysis only when the engine, model set and baseline fingerprint all match.
- **No probabilities, durations, latency, error rates, load redistribution or timelines.** There is
  no Monte Carlo and no randomness.
- **Execution is synchronous, bounded and stored.** Configurable limits (changes, failures, affected
  components, deltas) are checked before running, and the output is cut deterministically.
  Simulations are stored append-only (migration 0018) and audited as `architecture.simulated`.
  There is no job system.

## Consequences

- Results are **model-based projections of the declared architecture**. They are never guarantees
  of real-world performance, availability, cost or failure behavior, and every result carries that
  limitation. An architecture that declares little gets `unknown` impacts and `partial` or
  `unsupported` results: modeling becomes visible work.
- Adding a scenario type or an analysis means extending the catalog or registering an evaluator;
  the orchestrator does not change. Changing a type's semantics bumps its version, and comparisons
  across versions are refused.
- The web app's job model, presets, timelines, error rates and impact levels are replaced by the
  scenario, runs, entry impacts and deltas (docs/frontend/simulation-contract.md). Showing
  time-based or probabilistic behavior would need a validated model, specified in its own decision.
- A long-running or very large simulation would need a job system. The limits keep every simulation
  within one request until then.
