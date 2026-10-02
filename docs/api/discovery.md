# Discovery API

Discovery runs of a project: ArchitectOS reads **supplied artifacts** — Kubernetes manifests, Docker
Compose files, Terraform JSON (`.tf.json` or `terraform show -json`), Architecture IR JSON — and
proposes an architecture for a person to review. **Nothing is executed, evaluated, rendered or
fetched**: no Terraform plan, no template, no shell, no cloud account, no cluster. What the artifacts
declare is a **desired state**, not the running system: a declared replica count, endpoint or
dependency is not proof that it runs, is reachable or receives traffic.

The proposal is **not an architecture** until a person accepts it, explicitly, through the
architecture workflow: accepting creates a new architecture or a new revision (source `discovery`);
earlier revisions are never changed, and nothing is accepted automatically.

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /projects/{projectId}/discovery-runs` | `architecture.discover` | `DiscoveryRunRequest` | `201 DiscoveryRun` | `404 architecture_not_found, architecture_revision_not_found` (baseline), `409 project_archived`, `413 payload_too_large`, `422 invalid_discovery_request, validation_error`, `429 rate_limited` |
| `GET /projects/{projectId}/discovery-runs` | `architecture.read` | | `200 {runs, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/discovery-runs/{runId}` | `architecture.read` | | `200 DiscoveryRun` | `404 discovery_run_not_found` |
| `GET /projects/{projectId}/discovery-runs/{runId}/findings` | `architecture.read` | | `200 {runId, findings}` | `404 discovery_run_not_found` |
| `GET /projects/{projectId}/discovery-runs/{runId}/proposal` | `architecture.read` | | `200 Proposal` | `404 discovery_run_not_found`, `409 invalid_discovery_transition` |
| `POST /projects/{projectId}/discovery-runs/{runId}/decisions` | `architecture.discover` | `DecisionRequest` | `200 {runId, review}` | `404 discovery_run_not_found`, `409 invalid_discovery_transition, project_archived`, `422 invalid_discovery_request` |
| `POST /projects/{projectId}/discovery-runs/{runId}/accept` | `architecture.discover` + `architecture.create` or `architecture.update` | `AcceptRequest` | `201 Accepted` | `404 discovery_run_not_found, architecture_not_found`, `409 discovery_proposal_not_acceptable, architecture_version_conflict, invalid_discovery_transition, project_archived, architecture_archived`, `422 invalid_discovery_request, invalid_architecture` |
| `GET /projects/{projectId}/discovery-runs/{runId}/comparison` | `architecture.read` | | `200 RunComparison` | `404 discovery_run_not_found`, `409 invalid_discovery_transition` |
| `GET /projects/{projectId}/discovery-runs/{runId}/baseline-comparison` | `architecture.read` | | `200 BaselineComparison` | `404 discovery_run_not_found, architecture_not_found, architecture_revision_not_found`, `409 invalid_discovery_transition`, `422 invalid_discovery_request` |
| `DELETE /projects/{projectId}/discovery-runs/{runId}` | `architecture.discover` | | `204` | `404 discovery_run_not_found`, `409 accepted_discovery_run, project_archived` |

On every endpoint: `404 project_not_found` outside your organizations and `403 permission_denied`
without the permission. A run of another project or organization is `404 discovery_run_not_found`,
indistinguishable from a missing one. Viewers read runs; members, admins and owners run discoveries,
decide and accept (`architecture.discover`); accepting also needs `architecture.create` (a new
architecture) or `architecture.update` (a new revision). `GET …/discovery-runs` filters by `status`,
with `cursor` and `limit` (1–100, default 50), newest first, without results.

## Running a discovery

`DiscoveryRunRequest` (camelCase, unknown fields refused):
`{artifacts: [{path, content}], sourceType?, baseline?: {architectureId, revision}, label?}`.

- **Artifacts** are inline text: at most 50, 512 KiB each, 2 MiB in all. A `path` is a relative name
  (`k8s/shop.yaml`): an absolute path, a `..` segment, a backslash or a control character is refused
  (`422 invalid_discovery_request`, `details.reason: unsafe_path`). The content is read and **never
  stored**: a run keeps each artifact's path, SHA-256 and size.
- **`sourceType`** (`kubernetes`, `docker_compose`, `terraform_json`, `architecture_json`) applies to
  every artifact; otherwise each is detected. Native Terraform (`.tf`, HCL) is reported unsupported:
  supply Terraform JSON.
- **`baseline`**: a revision of an architecture of this project, to compare with later.

Running is synchronous. The run is stored `completed`, `completed_with_warnings` (anything unsupported,
ambiguous, unresolved, partly read or invalid) or `failed` — a discovery beyond the result limits
(entities, relationships, findings) is refused rather than truncated (`error.code`, e.g.
`too_many_entities`). Parsing is bounded (documents, depth, nodes, findings per artifact) and safe:
YAML aliases and custom tags are refused, templates (`{{ }}`) and `${…}` are kept as text.

## A run

`DiscoveryRun`: `{id, projectId, status, sourceType, label, baseline, requestedByUserId, requestedAt,
completedAt, summary, fingerprint, sourcesFingerprint, error, decisions, acceptances, note, artifacts,
entities, relationships, diagnostics, elements, validation, proposed, unresolved, extractors, review,
accepted}`.

- **`artifacts`**: path, content hash, size, status (`parsed`, `partial`, `unsupported`, `failed`),
  detected source type, format version, and the extractor and version that read it.
- **`entities`** (candidates): a key (also the IR node id), name, resource type, role (`component`,
  `routing`, `configuration`, `volume`, `network`), **`kind` — `null` when the source does not
  establish it**, the catalog `mapping` (`exact_match`, `mapped`, `ambiguous` with `candidates`,
  `unmapped`, `unsupported`, always with the rule and, if not confident, the reason) and the
  `configuration` mapped to IR properties — each with the value as written (`sourceValue`), the unit
  conversion applied (`transformation`), whether it is `valid` and, if not, the `problem`.
- **`relationships`**: explicit references (a name, a selector, a host in a connection string, a
  Terraform address, an IR connection), `resolved` to an entity of the same run or `unresolved` with
  the reason, the connection `kind` only where the source establishes it, and the resolution rule.
- **`diagnostics`**: parse errors, limits reached, unsupported constructs — stated, never dropped.
- **`elements`**: what became of each candidate in the proposal — `included` (with its IR element id
  and `origin`: `observed`, `inferred` or `user_provided`), `needs_review` or `excluded`, with the
  reason.
- **`proposed`** and **`validation`**: the proposal without review decisions, in Architecture IR form,
  and its structural and component-specification checks.
- **`summary`** counts things; there is **no confidence score**. `fingerprint` identifies the result
  (equal for identical inputs and versions); `sourcesFingerprint` identifies the inputs alone.
- **Findings** (`GET …/findings`, optionally `?entity=`): each extracted fact with its location
  (artifact, document, pointer, line), `verification` (`observed`, `user_provided`, `inferred`,
  `estimated`, `unknown`, `unsupported`), extractor and value as written — **a secret's value is never
  kept** (`redacted: true`). Fields present but not interpreted are listed by name
  (`uninterpreted_fields`).

## Review

`DecisionRequest`: `{subjectType: entity | relationship, subject, decision: accepted | rejected |
ignored, componentId?, nodeKind?, connectionKind?, comment?}`. The subject is an entity's key or a
relationship's id. Accepting may choose an ambiguous mapping's component (among its candidates) or
state a kind the source does not establish — **never one the source states** (`422`,
`details.reason: stated_by_the_source`). Decisions are history: the latest per subject applies. A
failed run cannot be reviewed (`409 invalid_discovery_transition`).

`GET …/proposal` computes the proposal now from the stored result and the decisions:
`{runId, note, architecture, contentHash, elements, validation, acceptanceProblem}`. A rejected or
ignored candidate is excluded; a connection whose endpoint is not a node is excluded or waits for
review.

## Accepting

`AcceptRequest`: `{proposalContentHash, architectureId?, baseVersion?, name?}`.

- `proposalContentHash` must be the `contentHash` of the proposal reviewed: if decisions changed it
  since, `409 discovery_proposal_not_acceptable` (`details.reason: proposal_changed`). A proposal
  with nothing to accept or structural errors is refused (`nothing_to_accept`, `structurally_invalid`).
- Without `architectureId`: a **new architecture** (named `name`, default the proposal's name),
  revision 1, source `discovery` — needs `architecture.create`.
- With `architectureId` and `baseVersion` (its current revision): a **new revision** of it — needs
  `architecture.update`; a stale `baseVersion` is `409 architecture_version_conflict` (nothing is
  merged or overwritten).

The answer `{runId, architectureId, revision, contentHash, createdArchitecture, createdRevision}` is
also recorded on the run (`accepted`). A run whose proposal was accepted cannot be deleted
(`409 accepted_discovery_run`): it is the provenance of that revision.

## Comparing

- `GET …/comparison?with={otherRunId}` compares the later run with the earlier, only on what both
  read alike: `comparability` is `comparable`, `partially_comparable` (limitations stated: an artifact
  supplied to one run only, an artifact partly read) or `not_comparable` (different extractor or rule
  versions, nothing read by both) — then no difference is reported. Differences (entities by key,
  relationships by source and target) are of **what the sources declare, not runtime drift**.
- `GET …/baseline-comparison` (default: the run's baseline; or `?architectureId=&revision=`) compares
  the reviewed proposal with a revision: nodes by id, connections by source, kind and target. An
  element or field of the baseline the sources do not describe is `not_in_sources` — **unknown, never
  removed**. Nothing is matched by name: a baseline sharing no node id is `not_comparable`.

## Audit

`discovery_run.created`, `discovery_run.reviewed`, `discovery_run.accepted` (with
`architecture.created` or `architecture.revised` from the architecture workflow) and
`discovery_run.deleted`, each with identifiers and counts only — never artifact content, values or
names.
