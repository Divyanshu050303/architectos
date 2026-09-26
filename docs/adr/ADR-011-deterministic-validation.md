# ADR-011: Deterministic validation: rules in code, stored runs, typed project policy

- Status: accepted
- Date: 2026-09-26

## Context

Milestone 6 adds validation of an architecture revision. The validation files in the repository
were empty placeholders; there was no component catalog, no project policy, and the web app's
proposed contract (health scores out of 100, finding triage) assumed engines that do not exist.
The Architecture IR already enforces structural integrity whenever an architecture is built.

## Decision

- **Deterministic, in-process rules.** Rules are Python classes registered explicitly
  (`engines/validation/registry.py`), each with a stable id and version, run in id order against an
  immutable context. No language model, no dynamic imports, no user-supplied expressions. The
  result is sorted, deduplicated and fingerprinted; equal inputs give equal fingerprints.
- **Failures are not findings.** A rule that raises or returns malformed output is recorded as a
  rule failure; the run completes with it listed. What no rule could check is stated as a
  limitation (`catalog_unavailable`, `no_policy`, `requirements_not_provided`).
- **Do not repeat the IR.** Its invariants stay where they are; rules report what the IR must allow
  (work in progress) but deserves attention.
- **No catalog yet.** Configuration is checked against the IR's property definitions only, and
  says so; nothing claims what a technology supports.
- **A minimal typed policy on the project** (allowed/prohibited technologies, allowed regions, TLS
  required, maximum components), replaced as a whole by owners and admins, enforced by mandatory,
  blocking rules. No policy language.
- **Verdicts from stated values only.** Four kinds of requirement are checked (regions and data
  residency, storage, retention, encryption in transit); every other requirement is
  `not_verifiable` with a reason. Unknown is never a pass.
- **Counts, not scores.** The summary counts findings by severity, category and blocking, and
  verdicts by kind. No 0–100 score and no triage state (`ignored`) are stored.
- **Synchronous, append-only runs.** A run executes in the request and is stored once, completed
  or failed, with its findings and the inputs it was given, in the transaction that audits it.
  `pending` and `running` exist in the model for a future worker.
- **The domain owns the contract.** Result types, options and the engine port live in
  `core/domain/validation`; the engine implements the port.

## Consequences

- A rule change is visible (new version, new rule-set version); stored runs keep the rule set they
  ran with and remain explainable after requirements or the policy change.
- The web app's health scores and triage are not provided; its alignment step maps its screens to
  counts and verdicts (docs/frontend/validation-contract.md).
- A background worker will need a migration: stored runs cannot be updated today.
- Adding capacity, reliability, security or cost checks means new rules (and, where they need it,
  new engines or a catalog), not a second framework.
