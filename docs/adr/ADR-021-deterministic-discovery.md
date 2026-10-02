# ADR-021: Deterministic discovery from supplied artifacts, accepted only by people

- Status: accepted
- Date: 2026-10-02

## Context

The Discovery Engine should help engineers understand an existing system: which components and
dependencies exist, how they connect, which configuration is declared, and how that compares with
the architecture ArchitectOS holds. Every discovery file in the repository was an empty scaffold; the
web app's proposed shape assumed live connectors to AWS accounts, Kubernetes clusters and Terraform,
started as background jobs, saving a discovered architecture in one call.

Discovery is where it is easiest to present a guess as a fact: a Deployment "is a service", an
environment variable "is a database connection", three declared replicas "are running", an image
"is PostgreSQL", a resource missing from a scan "was removed". Live scanning would also need
credentials, network access and production access this milestone does not have.

## Decision

- **Supplied artifacts only, never executed.** Kubernetes manifests, Docker Compose, Terraform JSON
  (`.tf.json`, `terraform show -json`) and Architecture IR JSON, sent inline (50 files, 2 MiB) and
  parsed by safe, bounded parsers (no YAML aliases, standard tags only, depth, document, value and
  finding limits). Nothing is executed, evaluated, rendered, expanded or fetched; native HCL and
  other constructs are reported unsupported. Artifact content and secret values are never stored.
  No new dependency; no live access.
- **Findings are evidence with provenance.** Every fact records where it was read, by which
  extractor version, and how it is known (`observed`, `user_provided`, `inferred`, `estimated`,
  `unknown`, `unsupported`); no confidence score. Fields read past are listed by name.
- **One architecture model, one catalog.** Candidates map to the existing component catalog by
  versioned rules (`exact_match`, `mapped`, `ambiguous`, `unmapped`, `unsupported`); configuration
  maps to IR properties with exact unit conversions, validated by the IR; the proposal is canonical
  Architecture IR, validated structurally and against component specifications.
- **Unknown stays unknown.** A node kind or connection kind the source does not establish is left
  for review; relationships come only from explicit references in the same format; nothing about
  runtime state, reachability, traffic or criticality is inferred; conflicting declarations are
  reported, not resolved; no default is filled in.
- **Synchronous, stored runs; per-candidate review; explicit acceptance.** A run is stored
  (migration `0021`, a trigger allowing only decisions and acceptances to change). People decide per
  candidate (accept, reject, ignore, choose among an ambiguous mapping's candidates, state a missing
  kind — never override the source). Accepting names the reviewed proposal's content hash and
  creates a new architecture or a new revision through `ArchitectureService` (source `discovery`,
  base version checked), recording the acceptance in the same transaction. Discovery never mutates
  the canonical architecture otherwise.
- **Comparison before drift.** Runs and revisions are compared only when comparable: different
  extractor or rule versions make results not comparable; coverage gaps narrow the comparison;
  what the sources do not describe is unknown, never removed.
- **Authorization.** `architecture.discover` (members and up) runs, reviews, accepts and deletes;
  accepting also needs `architecture.create` or `architecture.update`; viewers read; every lookup is
  scoped project -> run; audit entries carry identifiers and counts only.

## Consequences

- Many proposals start with workloads in `needs_review`: Kubernetes and Compose do not say whether a
  workload is a service or a worker, and people must say it.
- Teams supply rendered artifacts (Helm and Kustomize output, `terraform show -json`) rather than
  sources that need evaluation.
- The frontend must replace its connector-and-job shape with the run, review and acceptance contract
  ([docs/frontend/discovery-contract.md](../frontend/discovery-contract.md)).
- Live discovery and drift detection, if built, are separate capabilities consuming the stable
  result identities, versions and comparability rules defined here.
- See [docs/architecture/discovery-engine.md](../architecture/discovery-engine.md).
