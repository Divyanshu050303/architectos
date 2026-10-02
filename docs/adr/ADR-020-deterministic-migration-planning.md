# ADR-020: Deterministic migration plans as reviewed, versioned proposals

- Status: accepted
- Date: 2026-10-01

## Context

The Migration Planning Engine describes how to move an architecture from a current revision to a
selected target: steps, order, dependencies, risks, downtime, compatibility, data movement,
verification and rollback. Every migration file in the repository was an empty scaffold; the web
app's proposed shape carried a duration per step, risk levels, and step statuses `in_progress` and
`done`.

A migration plan is where unsupported confidence is most tempting: a duration nobody measured, a
"zero downtime" nobody can show, a rollback that ignores writes the new system accepted, a risk
score, a step marked done. The architecture records none of the data volumes, throughputs, schemas,
client libraries or traffic weights such claims would need.

## Decision

- **A plan is a proposal for review, never an execution.** No endpoint, status or record executes a
  step, provisions, copies data, switches traffic or rolls back; approval records a person's
  decision on one exact version. Execution belongs to a separate, explicitly authorized workflow.
- **Exact source and target.** A plan goes from an exact revision to a later revision of the same
  architecture, or to an evolution candidate rebuilt on that same revision; content hashes are
  recorded and checked. The IR's own diff is classified; no second architecture model exists.
- **Evidence-gated, versioned patterns.** Per-change templates (reconfigure, provision,
  decommission, reroute) and strategies supported only when the architecture declares their
  prerequisites: in-place (the default), rolling, blue-green, replication then cutover. Canary,
  expand-and-contract and strangler are registered as unsupported so a request is answered, never
  fabricated. Strategies are listed side by side, never scored; every step traces its change and
  pattern.
- **Unknown stays unknown.** Downtime, reversibility and compatibility have explicit unknowns;
  volumes and durations are missing and unevaluable; compatibility is verified only with
  machine-checkable evidence; checkpoints never pass without current modeled evidence and a
  planning-time pass is not proof of runtime success; risks are confirmed, potential (with
  preconditions) or unknown, never scored.
- **Explicit dependencies.** References and cycles are validated, transitions checked for their
  prerequisites, the order is deterministic, and nothing runs in parallel unless a rule says so.
- **Other engines through their stored analyses.** Matched by revision and content hash; stale
  evidence reported, never used; coverage per engine and side; comparisons only under the same
  assumptions (a cost change only with the same pricing snapshot and currency).
- **Append-only versions, exact review, staleness on read.** Migration `0020` stores one row per
  version; a trigger lets only the review status and history change. Regeneration appends a version
  and never silently replaces a reviewed or approved one; approval and rejection name the version
  and its fingerprint; a version whose revisions, models or evidence changed is stale and cannot be
  submitted or approved.
- **Authorization.** `migration.plan` (members and up) generates, regenerates, submits and archives;
  `migration.approve` (admins and owners) approves and rejects; viewers read. Every lookup is scoped
  project -> architecture -> plan; every change is audited with identifiers and counts only.

## Consequences

- Plans for stateful changes are often `needs_information` until people state the data scope and
  whether downtime is allowed: the plan says what is missing instead of guessing.
- A transition larger than one plan (more than 500 steps) is refused; it must be split.
- The frontend must replace its proposed migration shape (durations, risk levels, execution
  statuses) with the backend's contract ([docs/frontend/migration-contract.md](../frontend/migration-contract.md)).
- New patterns, step rules or evidence readings change the planner's versions, which marks existing
  plans stale (`models_changed`) rather than silently reinterpreting them.
- See [docs/architecture/migration-planning-engine.md](../architecture/migration-planning-engine.md).
