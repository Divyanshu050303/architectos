# ADR-015: Deterministic security analysis: declared controls, four kinds of finding, no score

- Status: accepted
- Date: 2026-09-27

## Context

Milestone 10 adds architecture-level security analysis. Every security file in the repository
(engine, rules, agent, prompts, knowledge) was an empty placeholder. The Architecture IR described
trust-zone boundaries, `tls`, protocols and direction, but not exposure, authentication,
authorization, encryption at rest, data classification, secret sources or audit logging. Security
requirements are stated in words, without structured constraints. The web app's proposed contract
assumed one project-level analysis with a 0–100 score and per-node boolean controls. A security tool
that guesses — reads a missing control as present, or a name as a boundary — gives false assurance.

## Decision

- **Inputs are optional IR properties** (`exposure`, `authentication`, `authorization`,
  `sensitive_operations`, `management_interface`, `data_classification`, `personal_data`,
  `encryption_at_rest`, `secrets_required`, `secret_source`, `secret_rotation`, `audit_logging`,
  `trust_level`; on connections `authentication`, `data_classification`, `personal_data`), with the
  IR's provenance. Nothing is defaulted, and nothing is inferred from names, technologies or
  providers: an absent control is **not modeled**.
- **Four kinds of finding, kept apart**: each finding type fixes its category and its basis —
  control gap, potential risk, violation, not evaluable — enforced by the domain and the database.
  Severity is a triage order on validation's scale; there is **no score** and no compliance claim.
- **The project's architecture policy is extended** with typed security fields, snapshotted with
  each analysis; validation's rules are unchanged.
- **Requirements are mapped by a documented keyword table** to fixed conditions (no language model);
  the mapping used is recorded; anything unmatched is unsupported and never passed. Policy and
  requirements share one judge: satisfied only on declared evidence.
- **Threats are STRIDE candidates** derived by a documented mapping from findings, naming what they
  derive from; no likelihood, exploitability or impact.
- **Secrets are never shown**: one redaction rule, shared with the architecture diff, applied to
  findings, checks, responses and storage, and enforced by the domain; failures are logged by type.
- **Analyzers in code, a generic orchestrator** (as the other engines): explicit selection,
  outputs checked against each analyzer's declaration, failures contained and reported.
- **Synchronous, stored, lock-free calculation**, like the other engines; findings are stored as
  rows and served in pages.

## Consequences

- An architecture is analyzed only as far as it declares its security: incompletely modeled ones get
  `not_evaluable` findings and `insufficient_input`, never an all-clear — modeling becomes visible
  work.
- A declared control is taken as named, not verified; implementation review, penetration testing and
  dependency scanning remain necessary (the documents and every result say so).
- Requirements outside the keyword table stay unsupported until they are rephrased or the table
  grows (a new, versioned rule).
- Discovery (Terraform, Kubernetes, cloud APIs) can later supply the same properties with their
  provenance without changing the engine or the contract.
- The web app's score and project-level analysis are replaced by per-architecture analyses with
  findings and checks (docs/frontend/security-contract.md).
