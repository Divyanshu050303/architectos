# Discovery engine

The discovery engine helps engineers understand an existing system by reading **architecture
artifacts they supply** — Kubernetes manifests, Docker Compose files, Terraform JSON, ArchitectOS
Architecture IR exports — and building an **evidence-backed proposal** of its architecture: what
components and resources are declared, how they reference each other, which configuration is
stated, which catalog components they correspond to, and what the artifacts do not establish.

**Static discovery reads a declared, desired state. It does not establish live runtime health,
reachability, traffic flow, deployment status or production behavior.** A manifest declaring three
replicas is evidence of a desired replica count in that manifest, not proof that three pods run.
Nothing is ever executed, evaluated, rendered, expanded or fetched, and **the canonical architecture
is never changed by discovery**: a proposal becomes an architecture revision only when a person
explicitly accepts it through the architecture workflow.

| Part | Where |
|---|---|
| Domain (requests, runs, findings, candidates, results, review, comparison) | `core/domain/discovery/{values,findings,results,runs,comparison}.py` |
| Safe parsing | `engines/discovery/loading.py` |
| Source adapters | `engines/discovery/{adapters,kubernetes,compose,terraform,architecture_json,sources}.py` |
| Normalization | `engines/discovery/normalize.py` |
| Catalog and configuration mapping | `engines/discovery/mapping.py` |
| Relationship discovery | `engines/discovery/relationships.py` |
| Proposed architecture | `engines/discovery/proposal.py` |
| Engine (pipeline, limits, versions) | `engines/discovery/engine.py`, port `core/domain/discovery/ports.py` |
| Service, persistence | `core/domain/discovery/{discovery_service,repository,serialization}.py`, `persistence/{models,repositories}/discovery.py`, migration `0021` |
| API | `apps/api/routes/discovery.py`, `apps/api/schemas/discovery.py`, [docs/api/discovery.md](../api/discovery.md) |

The top-level `discovery/` package and `workers/` remain empty scaffolds from the repository's
original layout (written for live cloud and cluster clients); they are not used — live scanning is
out of scope.

## Purpose and scope

Discovery answers, from the artifacts supplied: which components, services and infrastructure
resources are declared; how they are connected by explicit references; which configuration and
resource properties are stated; how each fact is known (observed, user-provided, inferred,
estimated, unknown, unsupported); which resources map to the component catalog; which evidence
supports each element; what cannot be determined; how the result compares with another run or an
architecture revision; and what needs a person's review before it is accepted.

Out of scope, by design: live cloud-account scanning, cloud credentials, cluster or production
access, runtime telemetry, provisioning, Terraform plan or apply, shell or source-code execution,
full static analysis, CVE scanning, full drift detection, automatic remediation, evolution or
migration, autonomous acceptance, a second architecture model, and any fabricated topology,
performance or deployment status.

## Supported source formats and versions

| Source type | Read | Extractor |
|---|---|---|
| `kubernetes` | Manifests (YAML or JSON, many documents, `kind: List`): Deployment, StatefulSet, DaemonSet, ReplicaSet, Job, CronJob, Pod, Service, Ingress, ConfigMap and Secret (key names only), PersistentVolumeClaim | `kubernetes@1` |
| `docker_compose` | Compose files: services (image, build, ports, replicas, resource limits and reservations, health check, labels, networks, environment variable names), named volumes and networks | `docker_compose@1` |
| `terraform_json` | Terraform configuration in JSON syntax (`.tf.json`) and `terraform show -json` output (state or plan; root and child modules up to 8 deep; `sensitive_values` honored) | `terraform_json@1` |
| `architecture_json` | ArchitectOS Architecture IR documents, read by the IR's own `from_dict` (schema upgrades and structural checks included) | `architecture_json@1` |

Reported, never guessed: native Terraform (`.tf`, `.tfvars` — HCL: `hcl_not_supported`), other
Kubernetes kinds (`unsupported_kind`), Compose `extends` and `include`, Terraform `module` calls
(their source is not supplied: `module_not_expanded`), provisioners and connection blocks,
`resource_changes`, unrecognized content (`unrecognized_format`). A format is claimed only where its
parser, validation, tests and documentation exist.

## Input requirements and processing limits

A run's artifacts are inline text: at most **50 artifacts, 512 KiB each, 2 MiB in all** (request body
up to 6 MiB, JSON-escaped). Paths are relative names — an absolute path, a `..` segment, a backslash
or a control character is refused. Artifact content is read and never stored: a run keeps each
artifact's path, SHA-256 and size.

| Limit | Value | When reached |
|---|---|---|
| YAML documents per artifact | 200 | `too_many_documents`, artifact failed |
| Nesting depth (YAML and JSON, JSON measured before parsing) | 32 | `too_deep` |
| YAML values per artifact | 100 000 | `too_large` |
| Findings per document | 5 000 | `too_many_findings` (error) |
| Findings / entities / relationships / diagnostics per result | 20 000 / 1 000 / 5 000 / 5 000 | the run fails (`too_many_entities`, …): refused, never truncated |
| Stored result | 32 MiB | the run fails (`too_large_result`) |
| Runs per person | 60 an hour | `429 rate_limited` |

YAML is read with a safe loader: aliases and anchors are refused (no expansion bombs), only standard
tags (`unsupported_tag` otherwise), YAML 1.2 core scalars (`80:80`, `no`, `012` and dates stay text),
duplicate keys reported, characters YAML forbids refused (`malformed_yaml`). JSON refuses `NaN` and
`Infinity`. Templates (`{{ }}`) and `${…}` are kept as text and reported (`template_not_rendered`,
`variable_not_expanded`), never rendered or expanded.

## Discovery workflow

1. **Read** (`read_artifacts`): each artifact is parsed, its format detected (or the requested source
   type used), and its adapter emits located findings; statuses `parsed`, `partial`, `unsupported`,
   `failed`.
2. **Normalize** (`normalize`): the findings about each declared resource become one entity — name,
   resource type, role, IR node kind where the source establishes it, namespace, properties.
3. **Map** (`candidates`): each entity to the component catalog and its properties to IR
   configuration.
4. **Relate** (`relationships`): each explicit reference resolved to an entity of the same run, or
   kept unresolved with the reason.
5. **Propose** (`propose`): the candidates as a canonical Architecture IR, validated, with what became
   of every candidate.
6. **Store** the run (`completed`, `completed_with_warnings` or `failed`), then **review**, **accept**,
   **compare**.

Running is synchronous, bounded by the limits above, on a worker thread with no transaction open;
the run is stored afterwards under the project lock and audited.

## Provenance and verification semantics

Every finding records its location (artifact, YAML document, path, line), the extractor and version,
its value as written, and how it is known:

| Verification | Meaning |
|---|---|
| `observed` | Directly present in a supported artifact |
| `user_provided` | Stated by a reviewer |
| `inferred` | Derived from evidence by a stated rule, not declared |
| `estimated` | Calculated from explicit inputs and a documented model (no rule produces it yet) |
| `unknown` | Not enough evidence to determine it |
| `unsupported` | Present but not interpreted (for example `uninterpreted_fields`) |

In the proposed IR, node, connection and field provenance carry the source (`kubernetes`,
`terraform`, `file_import`) and location, and **are never `verified`**; a field resting on an
inference is `inferred`; a field a reviewer stated is `user_edit`. There is **no confidence score**:
results count things. A secret's value is never kept — names that look like secrets, Terraform's
`sensitive_values`, Kubernetes Secret values and environment variable values (a connection string
contributes only its host) — the finding records only that a value exists (`redacted`).

## Component mapping behavior

Rule `discovery-catalog@1`, against the existing catalog (by id, never copied):

- **Terraform**: provider-specific types map exactly (`aws_sqs_queue` → `messaging/aws-sqs`,
  `exact_match`); managed databases and caches map by their declared `engine` (`mapped`) — an engine
  not stated literally stays `unmapped`; generic types (load balancers, API gateways, CDNs, virtual
  machines) map to the generic component; a provider the tables do not cover (not `aws`, `google`,
  `azurerm`) is `unsupported`.
- **Kubernetes and Compose**: by container image name (`postgres:16` → `databases/postgresql`),
  `mapped` — an image is evidence, not proof; containers mapping to different components are
  `ambiguous` (with the candidates, never chosen automatically); a service built from source is
  `unmapped`.
- **Architecture JSON**: the declared component (`exact_match`), else its technology.
- Supporting resources (routing, configuration, volumes, networks) are not components.
- A kind is adopted from the catalog only when the component describes exactly one kind (an
  inference); a stated kind the component does not describe leaves the entity unmapped.

Configuration (`discovery-configuration@1`): replicas, CPU and memory requests and limits, health
check, instance class and storage, with exact unit conversions (Kubernetes quantities, Docker byte
units, GiB) — each keeping the value as written and the conversion — checked by the IR's own
property specifications and the entity's kind. A value that fails, cannot be converted, or is
declared more than once (several containers or files) is kept invalid with the reason: no total and
no default is invented.

## Relationship discovery rules

Rule `discovery-relationships@1` follows only references the source states, within the same format:

- **Kubernetes**: names of Services, ConfigMaps, Secrets and PersistentVolumeClaims in the referring
  object's namespace; a Service's selector to the **one** workload whose pod labels contain it; a
  host in a connection string by cluster DNS (`api`, `api.shop`, `api.shop.svc`,
  `api.shop.svc.cluster.local`).
- **Compose**: `depends_on`, `links`, named volumes, a host naming a service of the project.
- **Terraform**: addresses in `${…}` and `depends_on` (attributes dropped; a resource with several
  instances, referenced as a whole, stays unresolved).
- **Architecture JSON**: declared connections.

A connection kind is set only where the source establishes it: `depends_on` is a `dependency` (a
need, with no communication stated), an Ingress routes HTTP `request`s to its Service, an IR
connection keeps its kind. Direction is the reference's. No reachability, traffic, synchronicity,
failover or criticality is inferred. An unresolved reference is a candidate with its reason, never
an IR connection.

## Proposed Architecture IR generation

Rule `discovery-proposal@1`, a pure function of the stored candidates, the review decisions and the
catalog — so a proposal is reproduced exactly from a stored result:

- **Nodes**: each component entity whose kind is known (source, catalog or reviewer); id = the
  entity's key; component = the confident mapping or the reviewer's choice; configuration = the
  valid mapped values. Unknown kind: `needs_review`. Supporting, rejected and ignored entities:
  `excluded`.
- **Connections**: each resolved relationship between two nodes whose kind is established or stated
  by a reviewer (else `needs_review`); a reference to a Kubernetes Service is followed to the one
  workload its selector resolves to (an inference, recorded with `via`); id = the relationship's id.
- **Validation**: the IR's own structural checks (an element the IR refuses is excluded and reported
  as an error) and, for nodes with a component, the constraint engine against the specification
  (violations and warnings as warnings, unknowns as information).
- Every candidate has a `ProposedElement`: `included` (with its origin `observed`, `inferred` or
  `user_provided`), `needs_review` or `excluded`, with the reason and its evidence.

## Review and acceptance workflow

The layers stay apart: **raw artifacts** (never stored) → **findings** → **candidates** (the
normalized model) → **proposal** (IR, not stored as an architecture) → **accepted architecture** (a
revision).

1. A person with `architecture.discover` records decisions per candidate: accept, reject or ignore;
   choose an ambiguous mapping's component among its candidates; state a node or connection kind the
   source does not establish — never overriding one it states. Decisions are history; the latest
   per subject applies.
2. `GET …/proposal` computes the proposal under the decisions, with its `contentHash`.
3. Accepting names that hash: a new architecture (`architecture.create`) or a new revision of an
   existing one on its current `baseVersion` (`architecture.update`) is created through
   `ArchitectureService.create` / `replace` with source `discovery`. The acceptance is recorded on the
   run **in the revision's own transaction**: both commit or neither. A changed proposal, a stale
   base, an empty or structurally invalid proposal is refused. Earlier revisions are never changed.

## API contracts

[docs/api/discovery.md](../api/discovery.md): `POST/GET /projects/{id}/discovery-runs`,
`GET/DELETE …/{runId}`, `GET …/findings`, `GET …/proposal`, `POST …/decisions`, `POST …/accept`,
`GET …/comparison?with=`, `GET …/baseline-comparison`. Typed camelCase models; unknown request fields
refused; the error envelope and pagination of every other endpoint.

## Persistence and authorization

Migration `0021` creates `discovery_runs`: project and same-project baseline revision foreign keys,
checks that a completed run has a result, summary and fingerprints and a failed run an error, a
size limit, indexes for listing (`project_id, requested_at, id`) and baselines. A trigger lets only
`decisions` and `acceptances` change, refuses deleting a run with an acceptance (it is the provenance
of that revision) and truncation. Stored results are verified against their fingerprint when read
back. Runs live as long as their project (a soft-deleted project hides them); an unaccepted run can
be deleted explicitly.

Every operation goes through `project_access` (`404 project_not_found` outside the organization,
`403 permission_denied` without the permission) and then by (project, run) id — a run of another
project or tenant is `404 discovery_run_not_found`. `architecture.discover` (members and up) runs,
decides, accepts and deletes; accepting also needs `architecture.create` or `architecture.update`,
checked by the architecture workflow; viewers read. Audit entries (`discovery_run.created`,
`.reviewed`, `.accepted`, `.deleted`) carry identifiers and counts only.

## Security considerations

- Artifacts are untrusted: parsed by safe, bounded parsers; nothing in them is executed, evaluated,
  rendered, expanded, imported or fetched. The domain and engine import no network, process or
  storage module (enforced by tests that scan the code and trap process and socket calls).
- Paths are names, never locations; content is never written to disk or the database.
- Secret values are never kept; findings record their presence.
- Parse failures and limits produce stated diagnostics with no internals; an unexpected engine error
  is a `500 internal_error` that stores nothing and exposes nothing.
- Request body, rate and result size limits bound the work; tenant isolation is enforced on every
  lookup.

## Unsupported constructs and known limitations

- Native HCL, Helm charts (unrendered), Kustomize overlays, Compose `extends`/`include`, Terraform
  modules whose source is not supplied, Kubernetes custom resources and other kinds — reported, not
  read.
- References across formats are never resolved (a Kubernetes host is not matched to a Terraform
  database); hosts outside the artifacts stay unresolved.
- A Deployment's or Compose service's kind (service or worker) is not established by the source and
  needs review; a virtual machine's too.
- Resource values declared on several containers are not summed; the per-instance total is unknown.
- Only the mapping tables' types and images map to the catalog; everything else is `unmapped`.
- Nothing about runtime state is known: health, reachability, traffic, real replica counts.
- The `estimated` verification exists for future models; no rule produces it yet.

## How drift detection consumes discovery results

Discovery exposes stable contracts for a later Drift Detection engine, without implementing it:

- **Result identity**: `fingerprint` (the whole result: equal for identical inputs and versions) and
  `sourcesFingerprint` (the inputs alone: each artifact's path and content hash).
- **Versions**: `extractors` records every extractor and rule version used.
- **Comparability first** (`compare_results`): different versions make two results not comparable
  (no difference is reported); artifacts supplied to one run, or not fully read, narrow the
  comparison with stated limitations. Differences are of the declared state.
- **Against a revision** (`compare_with_baseline`): nodes by id, connections by (source, kind,
  target); what the baseline has and the sources do not describe is `not_in_sources` — unknown,
  never "removed". Each comparison cites both sides (fingerprints, content hashes).

## Adding a source adapter

1. Add a `SourceType` and an adapter implementing `SourceAdapter` (`source_type`, `version`,
   `detect`, `extract`) that reads parsed documents only — never the network, the file system or a
   process — and emits findings through an `Emitter` (entities, properties, references, unsupported
   constructs, uninterpreted field names; secrets redacted by name).
2. Register it in `engines/discovery/sources.py` (detection order: most specific first) and its IR
   provenance source in `IR_SOURCES`.
3. Add its kind and role rules in `normalize.py` (a new rule version), its key format and reference
   forms in `relationships.py`, and any catalog or configuration rules in `mapping.py`.
4. Bump the version of any rule whose output changes: stored results keep the versions that produced
   them, and comparisons across versions are refused.
5. Add tests (adapter, normalization, mapping, relationships, fixtures, safety) and document the
   format here and in the API page.

## Tests

```
uv run pytest tests/unit/discovery                                  # domain, parsers, adapters, mapping, relationships, proposal, comparison, fixtures
uv run pytest tests/integration/api/test_discovery.py               # API, persistence, acceptance, isolation (needs `make db-up`)
uv run pytest tests/security/test_discovery_safety.py tests/security/test_traceability_discovery_engine.py
make lint typecheck test-unit test-integration test-security test-eval migrate-check
```

## Example: a supported source produces component findings

```yaml
# compose.yaml
name: shop
services:
  db:
    image: postgres:16
```

One entity `compose:shop/service/db`: role `component`, mapping `mapped` to `databases/postgresql`
(rule `discovery-catalog@1`, from the image), kind `database` adopted from the catalog (inferred),
technology `postgresql` `16`. The proposal includes node `compose:shop/service/db` with origin
`inferred`; its `component` and `kind` fields are marked `inferred`, nothing `verified`.

## Example: an explicit reference produces a candidate relationship

A Kubernetes Deployment's container sets `CACHE_URL=redis://cache.shop.svc:6379` and a Service
`cache` exists in namespace `shop`: the relationship `kubernetes:shop/deployment/orders` →
`kubernetes:shop/service/cache` is `resolved` by cluster DNS, its `kind` unknown. In the proposal it
is followed through the Service to the StatefulSet its selector names (`via`) and stays
`needs_review`: a reviewer states what the Deployment and the StatefulSet are, then the connection
kind — access is not assumed to be a request or data access.

## Example: an ambiguous mapping requires review

A Pod with containers `redis:7` and `postgres:16`: mapping `ambiguous`, candidates
`databases/postgresql` and `databases/redis`, no component and no kind chosen. A reviewer accepts it
with `componentId: databases/redis` (one of the candidates) and `nodeKind: cache`; any other
component is refused.

## Example: missing evidence remains unknown

The Compose database above states no `max_connections`: the node's configuration has no value for
it (no default is filled in), and validation reports `constraint_cannot_evaluate` — PostgreSQL's
analysis needs `max_connections` — as information, never as a pass.

## Example: an unsupported construct is reported without fabricated output

`kind: Widget` (a custom resource) produces a diagnostic `unsupported_kind` and an `unsupported`
finding — no entity, no node; `main.tf` (HCL) produces `hcl_not_supported` and the artifact is
`unsupported`; a `cloudflare_record` is an entity whose mapping is `unsupported`.

## Example: a discovery proposal is accepted through the architecture lifecycle

`GET …/proposal` returns `contentHash: 3f…`. `POST …/accept {"proposalContentHash": "3f…",
"architectureId": "…", "baseVersion": 4}` creates revision 5 through `ArchitectureService.replace`
with source `discovery` and reason "Accepted from discovery run …"; the run records the acceptance
in the same transaction. A `baseVersion` other than the current revision is
`409 architecture_version_conflict`.

## Example: the canonical architecture remains unchanged before approval

Running a discovery with a baseline revision, reading its proposal, recording decisions and
comparing it with the baseline change nothing: the architecture's current revision and every
revision's content hash stay as they were until a person accepts; even then, only a new revision is
added.

## Repository audit

Before this work, every discovery file was an empty scaffold: `discovery/{aws,common,kubernetes,
terraform}/**`, `workers/*`, this document and `tests/integration/discovery/`; no route, table,
permission or test. The web app mocked live connectors (`apps/web/api/discovery.ts`). Reused: the
Architecture IR (model, provenance, `from_dict`, structural checks, diff, secret-path rule), the
component catalog and constraint engine, `ArchitectureService` with `RevisionSource.DISCOVERY`,
project access, audit, pagination, body and rate limits, the append-only guard pattern, PyYAML's
safe loader. No dependency was added.

## Final review

- No live access, credentials, execution or second architecture model was introduced.
- Unknown stays unknown: kinds, connection kinds, configuration and comparisons say what they cannot
  establish.
- The proposal is reproducible from the stored result and the versions recorded with it.
- The canonical architecture changes only through an explicit, authorized acceptance that adds a
  revision.
- Frontend alignment is a separate step: [docs/frontend/discovery-contract.md](../frontend/discovery-contract.md).
