# ADR-007: Requirement versioning, requirement sets and the planning-input contract

- Status: accepted
- Date: 2026-09-25

## Context

Architectures, validation results and simulations will be computed from requirements that keep
changing ("2K RPS" becomes "5K RPS"). ArchitectOS must always be able to say which requirements a
result was computed from, and reproduce them. Future engines must not depend on HTTP, SQL, LLMs or
cloud resources, nor on how requirements happen to be stored.

## Decision

- **Every change is a version.** `requirements` holds the current state (for listing and search);
  `requirement_versions` holds every state, append-only (a trigger rejects any modification or
  removal, like `audit_logs`). A deferred composite foreign key guarantees the current state exists
  as a version. Changes carry `expectedVersion` (optimistic concurrency) and, for requirements in
  force, a `change_reason`. Deletion is soft.
- **Stored history is not re-validated on load**, so rules can tighten without breaking history;
  `validate` finds requirements that no longer pass.
- **Requirement sets** are immutable, numbered snapshots pinning `(requirement, version)` pairs.
  Composite foreign keys make the database guarantee a set, its items and their requirements share
  one project, and that each pinned version exists. Only valid requirements in force can be pinned;
  conflicting requirements cannot form a set.
- **The Architecture Planning Input** is a versioned domain contract (`schema_version` 1) built from a
  set, with constraints in canonical units. It is **stored** with the set rather than re-derived on
  read, and its SHA-256 over canonical JSON (sorted keys, no whitespace, UTF-8) is the set's content
  hash.

## Consequences

- An architecture references a requirement set; its inputs are reproducible byte for byte, even if
  the normalization code changes later, and anyone can verify the hash.
- Storage grows with every change (full snapshots, no diffs) and with every set (the planning input
  includes statements; at most 1,000 requirements and 16 MiB per set). Acceptable at the expected
  scale; archival can come later without changing the model.
- The contract's shape is frozen per schema version: engines can rely on it, and a change means a new
  version handled side by side.
- Production should also restrict the application role to inserting and reading on the append-only
  tables.
