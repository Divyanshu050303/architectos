# ADR-019: A component catalog of reviewed, versioned specifications with provenance

- Status: accepted
- Date: 2026-09-28

## Context

ARCH-COMP-001 asks for a component knowledge base: machine-readable specifications of
infrastructure technologies, constraint evaluation, and consistent metadata for the analysis
engines. Every component, constraints and knowledge file in the repository was an empty
placeholder. The Architecture IR already had the slot — a node's optional `component` catalog path
and its `technology` — and its property specifications with units in names. The Validation and
Capacity engines reported `catalog_unavailable`; the web app hardcoded palette defaults.

A catalog is where invented numbers enter a system most easily: typical throughputs, "default"
limits remembered rather than read, a recommendation recorded as a limit, a capability assumed
because another product of the category has it. Checking sources while writing the initial entries
found values that had changed from common knowledge (SQS messages now up to 1 MiB; S3 objects up to
50 TB).

## Decision

- **Specifications are reviewed files in the repository**, YAML read as untrusted input (safe
  loader, no aliases, bounded, every key checked, exact numbers), loaded once at startup into a
  read-only catalog. No database table and no mutation endpoint: a change is reviewed like code.
- **Every claim carries provenance** — `documented` (a cited source with the date it was
  retrieved), `user_configured`, `measured`, `estimated` (with its model or assumptions),
  `inferred` (with its reasoning), `unknown` — and the domain refuses mixing them up: a limit or a
  default must be documented; an estimate is never a measurement; unknown is never filled in.
- **Support status is explicit and checked**: `planned` entries claim nothing; `partial` and
  `supported` state what their status requires. The initial catalog lists the 39 requested
  technologies; five are `supported` and one `partial`, each claim checked against the official
  page; the rest are `planned`.
- **Versions are immutable once published**: a lock records every version's content hash; an edit
  in place, a removed version or an unrecorded one is refused; older versions stay readable; every
  evaluation records the versions (`ref` → hash) it used.
- **One architecture model**: configuration fields and constraints refer to Architecture IR
  properties; a node links to a specification by `component`, never by matching names.
- **Constraints are typed by how binding their source is** (`hard_limit`, `configurable_limit`,
  `conditional_limit`, `recommended_range`, `unsupported_configuration`, `unknown`); a
  recommendation or raisable default is never more severe than `medium`. Evaluation outcomes are
  `pass`, `warning`, `violation`, `cannot_evaluate` (never a pass) and `not_applicable`; only
  documented constraints are evaluated, and no throughput is concluded from a configuration.
- **Integration starts with validation**: a rule reports the evaluation of nodes that refer to a
  component, validation runs record the specification versions, and a stateless endpoint evaluates
  a configuration. The other engines do not read the catalog yet.

## Consequences

- A specification states documented facts and their sources, not guarantees of a deployment's
  performance or capacity; most entries say little until their sources are checked, and nodes
  referring to them get `cannot_evaluate` — visible work, not a silent pass.
- Adding a technology or a claim means reading its source; changing one means a new version and a
  lock update. Recorded evidence ages with its retrieval date.
- Numbers without an IR property (message size, timeouts, object size) are capacity dimensions, not
  evaluated constraints, until the IR models them.
- Connecting the capacity, cost, reliability, security, observability, simulation and evolution
  engines is future work, each citing the specification versions it reads; none keeps a competing
  catalog.
- The web app's hardcoded palette and defaults are replaced by the catalog API in the frontend step
  (docs/frontend/component-contract.md).
