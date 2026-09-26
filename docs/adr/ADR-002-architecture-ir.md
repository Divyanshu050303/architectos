# ADR-002: One canonical Architecture IR, stored as immutable revisions

- Status: accepted
- Date: 2026-09-26

## Context

Every ArchitectOS capability reasons about an architecture: generating it, sizing it, validating
it, simulating it, discovering it from real infrastructure, detecting drift, planning its
evolution. If each built its own model, they would disagree, and no result could be traced to the
exact architecture it was computed from. Language models propose architectures but are not a
source of truth; discovered infrastructure is partial; people edit concurrently. The repository
had two empty homes for the model (`core/domain/architecture/` and `core/architecture_ir/`), and
the web app a proposed wire contract but no backend.

## Decision

- **One model.** `core/architecture_ir/` holds the canonical IR as pure, immutable values (nodes,
  connections, typed configuration, provenance, requirement and decision references, assumptions)
  with no dependency on HTTP, storage or any LLM. `core/domain/architecture/` holds only its
  lifecycle (the project's architecture, revisions, the service). The duplicate placeholders were
  removed. Engines read it through `Topology`; the Architecture Engine must produce it.
- **Valid by construction.** Structural rules (unique ids, no dangling references, explicit rules
  for self-connections, duplicate connections, cycles and containment, typed and applicable
  properties) are enforced when an IR is built, reporting every violation with element, id, field,
  rule and message. Structural validity makes no claim about quality.
- **Honest about knowledge.** Provenance (source, reference, confidence, verified, inferred)
  on the architecture, each element and each field; explicit unknown values distinct from absent
  ones; unrecognized settings preserved, not dropped. Model proposals and system defaults can
  never be marked verified.
- **Canonical JSON, versioned format.** One deterministic JSON form (sorted, exact decimals as
  strings) with a content hash, a strict reader (unknown fields refused), a generated JSON Schema,
  and an explicit `schema_version` with pure upgrades applied on read.
- **Immutable revisions, identity-based diff.** A project has one architecture whose content lives
  in append-only, numbered revisions (PostgreSQL JSONB, trigger-protected), each with parent,
  source, summary, reason, hash and the requirement set it was designed against. Edits are applied
  as commands to the current revision only (optimistic concurrency, no silent merge). The diff
  matches elements by id, never by name or position.
- **Layout is not architecture.** Positions are stored beside the architecture, never versioned,
  never diffed, never audited.
- **The API carries the IR as is**, inside a camelCase envelope, instead of reshaping it into a
  second, API-only model.

## Consequences

- Any result (a simulation, a validation run, a decision) can cite an exact revision and content
  hash; history is never rewritten.
- New node kinds and configuration properties are additive and backward compatible; any other
  change to the format needs a schema version and an upgrade function, with the JSON Schema
  regenerated and tested.
- Storing whole snapshots costs storage per revision (bounded at 16 MiB each) but keeps revisions
  immutable, self-contained and schema-evolvable; querying across nodes in SQL is not a goal.
- The web app's proposed contract (node `position`, edges with `source`/`target`, free-text
  technology, twelve node kinds) differs from the IR; the frontend alignment step adopts the IR
  (see docs/frontend/architecture-contract.md).
