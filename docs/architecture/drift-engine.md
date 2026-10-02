# Drift Detection Engine

Drift detection compares **one exact architecture revision** (the baseline) with **one stored
discovery run** (the observed state). It reports each difference with:

- what it rests on: evidence and source locations;
- how far that evidence goes: its classification;
- what the comparison could not see: compatibility and coverage.

It is deterministic and read-only. It runs no live scan, executes nothing, never remediates and never
changes the architecture.

> Drift detection identifies differences **within supported comparison coverage**. It does not
> establish that a change is unauthorized, harmful, insecure or operationally significant without
> additional evidence. It does not establish runtime state either: the observed state is what the
> artifacts **declare**, not what is running.

Code:

- `core/domain/drift`: contracts, items, review, the service and the ports.
- `engines/drift`: sources, compatibility, matching, comparison, classification, impact and the
  engine.
- `persistence/repositories/drift.py`: storage. The tables are created by migration `0022`.
- `apps/api/routes/drift.py`: the API. Its contract is in [docs/api/drift.md](../api/drift.md).

The decisions are recorded in [ADR-022](../adr/ADR-022-deterministic-drift-detection.md).

## Purpose and scope

The engine answers one question: **within what this discovery run read, how does what the sources
declare differ from this revision?** The answer covers:

- components and connections added, removed or modified;
- configuration and resource values changed;
- what could not be decided, and why.

People then follow each difference as a **drift item** and review it.

In scope:

- comparing a stored revision with a stored discovery run;
- matching identities, by stable identifiers or identities people confirmed;
- typed comparison of declared properties;
- coverage-aware classification;
- context from stored downstream analyses;
- a review workflow with history;
- the API, persistence, authorization and audit.

Out of scope:

- live cloud or cluster scanning, and runtime telemetry;
- scheduling and continuous monitoring;
- remediation, rollback and architecture mutation;
- deployment and provisioning;
- CVE or security scanning;
- severity rankings and any drift score;
- claims that a change was unauthorized.

## Baseline and discovery input requirements

- **Baseline**: an architecture of the project and an explicit revision number. It is never "the
  latest": the analysis records that revision's content hash and IR schema version.
  - Elements accepted from a discovery carry provenance (`actor: discovery:<source type>`,
    `reference: <path>#<document>:<pointer>`). That provenance says which artifact each element was
    read from, and it is what allows a removal to be confirmed.
  - Elements written by a person carry no discovery source. Whether they still exist can't be
    established from artifacts.
- **Observed state**: a discovery run of the same project that has a result.
  - The analysis uses the run's stored entities and relationships: what the sources declare,
    including entities whose kind is unknown. The analysis does not use the run's review-dependent
    proposal, so later review decisions never change a past analysis.
  - A run that any analysis compared can't be deleted (`discovery_run_in_use`).
- **Identity mappings**: the confirmations in force for the architecture when the analysis runs.
- **Exclusions**: up to 500 baseline element ids or discovery keys to leave out of both sides.
- **Policy**: only `default`.

## Compatibility and coverage semantics

Compatibility is decided **before** any difference is computed (rule `drift-compatibility@1`). This
ensures that a parser or schema change is never read as an architecture change, and that missing
coverage is never read as a removal. Each dimension has one of five outcomes: `compatible`,
`compatible_with_warnings`, `partially_comparable`, `cannot_determine`, `incompatible`. The overall
outcome is the least comparable dimension.

| Dimension | Checks |
|---|---|
| `ir_schema` | The revision is in the current IR schema. An older schema is compared after upgrading (warning); a newer one can't be compared. |
| `discovery_result` | The run's result is in the version the engine reads. |
| `extractor_versions` | The run used the same extractors and rules as the run the baseline was accepted from. A changed shared rule (mapping, configuration, relationships, proposal) makes the inputs incompatible. A changed extractor or kind rule makes that source type not comparable. |
| `source_types` | The run reads the source types the baseline's discovered elements came from. |
| `source_coverage` | Every supplied artifact was read completely, with nothing unsupported or unresolved. |
| `artifact_coverage` | Every artifact a baseline element was discovered from was read completely. |
| `identity` | Baseline nodes share identifiers, or confirmed mappings, with discovered entities. If none do, the outcome is `cannot_determine`. |
| `freshness` | The baseline is the current revision, and the run isn't older than it. Otherwise a warning: the comparison is exact, but may be out of date. |

When the inputs are **incompatible**, nothing else is compared. The analysis is stored as
`incompatible_inputs`, with one `comparison_incompatible` finding per incompatible dimension.

**Coverage** lists the artifacts that were:

- `inspected`: read completely;
- `partial`: read in part;
- `unread`: not read, because they were unsupported or failed.

It also counts unsupported constructs, unresolved entities and relationships, and errors.
`noDifferenceWithinCoverage` is true only when nothing differs within the inspected scope. It never
means "no drift".

## Identity matching rules

Rule `drift-identity@1`. Matching is deterministic and **never by name**.

**Nodes** are matched in this order:

1. `same_id`: the node id is the entity's discovery key. A discovery key is a stable source
   identifier made of the format, the namespace or project, the resource type and the name.
2. `confirmed_mapping`: a person confirmed that the node and the entity are the same, for example
   after a rename.
3. Otherwise `unmatched`. There is one exception: when an unmatched entity now stands where the node
   was discovered (the same artifact, document and pointer), it's a candidate rather than a match,
   and is reported as `ambiguous` until a person confirms it.

A discovery key that several nodes claim (an id plus a mapping, or two mappings) is `ambiguous` for
each of those nodes. Nothing is merged.

**Connections** are matched only between matched endpoints, in this order:

1. by relationship id (`same_id`);
2. by endpoints (`signature`), following a Kubernetes Service to the workload it selects;
3. by a kind the relationship states, when several connections share the same endpoints. Otherwise
   they're `ambiguous`.

Relationships to supporting resources (configuration, volumes, networks) aren't connections.

## Supported structural and configuration comparisons

Rule `drift-differences@1`. Matched nodes are compared through the IR's own diff, so values are typed
and canonical: `0.50` equals `0.5`.

| Compared | When |
|---|---|
| `kind` | Only when the source establishes one. A kind the catalog implies is marked inferred. |
| `component` | Only when the mapping is confident. An image-based mapping is inferred. |
| `technology` | Only what the source states. An unstated version is unknown, not removed. |
| Configuration and resources | Only the properties the source type can declare (below). |

The properties each source type can declare:

| Source type | Properties |
|---|---|
| Kubernetes, Docker Compose | replicas, CPU and memory requests and limits, health check |
| Terraform JSON | instance class, storage |
| Architecture IR JSON | every property |

How a property is compared:

- A declared value that differs from the baseline's is a change.
- A value the baseline lacks is reported as added.
- A baseline value that is no longer declared is reported as no longer declared, never filled in with
  a default: not declaring a value doesn't prove it is unset.
- A value discovery found invalid is `unresolved_difference`.
- Properties a source type can't express aren't compared.

Structural changes:

- Unmatched baseline nodes and connections are removal candidates.
- Unmatched discovered components and relationships are additions.
- A connection's kind is compared only when the source states one.

The finding types are `component_added`, `component_removed`, `component_modified`,
`connection_added`, `connection_removed`, `connection_modified`, `configuration_changed`,
`resource_changed`, `mapping_changed`, `unresolved_difference`, `coverage_changed` and
`comparison_incompatible`.

## Finding classification semantics

Rule `drift-classification@1`. Every finding says how far its evidence goes, and never further.

| Classification | Meaning |
|---|---|
| `confirmed` | Comparable baseline and discovery evidence supports it. |
| `potential` | The evidence suggests it, but identity or coverage is incomplete, or it rests on an inference (a catalog kind, an image) or on a value that is no longer declared. |
| `not_comparable` | Differences in the inputs prevent a reliable comparison: incompatible dimensions, or a source type whose extractor changed. |
| `unknown` | The evidence is insufficient: an ambiguous identity, an unreadable value, or a scope that wasn't inspected. |

An **incompatible** comparison has no differences at all, only `comparison_incompatible` findings.

The analysis status reflects all of this:

| Status | When |
|---|---|
| `completed` | Every finding is confirmed, and coverage and compatibility have nothing to qualify. |
| `completed_with_warnings` | Anything is less certain or less complete than that. |
| `incompatible_inputs` | The inputs couldn't be compared. |
| `failed` | The engine refused its own result. |

## Removal classification requirements

A baseline element with no match is:

| Classification | When |
|---|---|
| `confirmed` | The element was discovered from an artifact that this run **read completely**: the scope was inspected, and the element isn't there. |
| `potential` | That artifact was read only in part. |
| `unknown` | That artifact wasn't supplied or wasn't read, or the element was written by a person (no artifact names it). |

Every artifact a baseline element rests on that wasn't read completely also gets a `coverage_changed`
finding, with classification `unknown`: it marks the scope left uncompared. **Absence is not
removal.**

## Finding lifecycle and review actions

### Finding and item identity

- **Finding ids** are stable: `id = digest("dft", type, subject, path)`. The same difference has the
  same id in every analysis.
- **Items** are keyed by `itemKey = digest("dfi", element, subject, path)`, so a type change of the
  same property stays the same item.

### Items across analyses

- Each analysis that finds a difference again appends a `detected` event to its item, and reopens the
  item if it was resolved.
- Items are never deleted, and their history only grows.

### Review actions

People review items. Review is separate from what the comparison found.

| Action | Moves the status to | Needs |
|---|---|---|
| `acknowledge` | `acknowledged` | |
| `investigate` | `investigating` | |
| `accept` | `accepted` (expected; the baseline isn't updated) | |
| `dismiss` | `dismissed` | a reason |
| `resolve` | `resolved` | evidence (see below) |
| `reopen` | `reopened` | a note |
| `note` | unchanged | a note |
| `link` | unchanged | a decision, migration plan, evolution analysis or revision **of the same project** |

A resolution needs one of these:

- A later analysis of the same architecture that is comparable, inspected the item's artifacts, and
  no longer detects the item. Otherwise the action is refused with `another_architecture`,
  `not_compared`, `outside_coverage` or `still_detected`.
- A revision link.

Every action is recorded with who, when and what it rested on, and audited.

## Evidence and provenance references

Each finding carries the following references:

| Field | Contents |
|---|---|
| `evidence` | Discovery finding ids. |
| `locations` | Source references in the form `path#document:pointer`. |
| `baselineReference` | The baseline element's own provenance. |
| `baselineId` and `discoveredKey` | The compared elements. |
| `match` | How the elements were matched. |
| `rule` | The rule that produced the finding, with its version. |
| `limitations` | What the finding doesn't establish. |

The values are canonical JSON. A secret path's values are never kept: `redacted: true` says that it
changed.

The result records:

- `baseline`: the architecture, revision number, content hash and schema version;
- `observed`: the run, the result and sources fingerprints, and the extractor versions;
- `versions`: every rule version used.

The result's fingerprint covers all of its content and is verified whenever the result is read.

## Downstream engine integrations

Rule `drift-impact@1`. Each finding names the engines whose models read what it concerns, and links
the stored analyses of the **baseline revision** by those engines. This is context, never an impact
claim, and nothing is recomputed.

Which engines a finding names depends on what changed:

| What changed | Engines named |
|---|---|
| A configuration or resource value | The engines the component's catalog specification lists as reading that property. For a node without a component, the catalog-wide list, stated as such. |
| Structure or component | Validation. |
| A connection | Capacity, reliability and security. |
| A scope or not-comparable finding | None. |

Each linked analysis has one of these states:

| State | Meaning |
|---|---|
| `current` | The analysis is of the exact revision and content. It comes with that analysis's own items about the element. |
| `stale` | The analysis is of other content. It is named, but shouldn't be relied on. |
| `missing` | No analysis is stored. |

Requirements the element references, and decisions that name it as a subject, are listed for review.
A requirement is never marked violated, and a decision is never marked invalid. Drift detection works
without any downstream analysis. A consequence of the discovered state stays unknown until that
engine analyzes a revision holding it.

## API contracts

There are 10 endpoints under `/api/v1/projects/{projectId}`, documented in
[docs/api/drift.md](../api/drift.md):

- run, list and read analyses, and read their findings;
- list and read drift items, and review one;
- list and confirm identity mappings.

Requests are typed camelCase, and unknown fields are refused. Errors use the standard envelope. The
drift error codes are `invalid_drift_request`, `drift_analysis_not_found`, `drift_item_not_found`,
`invalid_drift_review_action` and `discovery_run_in_use`.

## Persistence and authorization

Migration `0022` adds three tables. Every row has same-project foreign keys to the architecture, the
exact revision, the discovery run and the analyses.

| Table | Contents | Guard trigger |
|---|---|---|
| `drift_analyses` | The request and the result, or the error. | Append-only: any update, deletion or truncation is refused. |
| `drift_items` | Each item, unique per architecture and key. | Identity columns fixed, history only grows, never deleted. |
| `drift_identity_mappings` | Each confirmation or retraction. | Append-only. |

Every read is scoped by project. Another project's records return `404`, indistinguishable from
missing ones. Permissions:

| Who | Permission | Can |
|---|---|---|
| Members, admins, owners | `architecture.drift` | Run analyses, review items, confirm identities. |
| Viewers | `architecture.read` | Read analyses, items and mappings. |

Running an analysis is rate-limited to 60 per hour per user, and the body is limited to 64 KiB. The
audit events are `drift_analysis.created`, `drift_item.reviewed` and `drift_identity.confirmed`. They
record identifiers, statuses and counts only, never notes, values or names.

## Security considerations

- **No live access:** nothing is scanned, fetched or executed. A test scans the drift code's syntax
  tree for imports and calls that execute or fetch.
- **Read-only access to other records:** drift code may only read architectures and discovery runs. A
  test enforces this, and nothing in drift imports the architecture write service.
- **Isolation:** the baseline revision, the discovery run, evidence analyses and every link target
  must belong to the same project. That prevents using a valid id from another project or tenant.
- **Secrets** are never stored, returned or audited. Discovery already drops them, and drift redacts
  any secret path.
- **Failures:** an engine refusal is stored as a `failed` analysis. An unexpected error returns
  `500 internal_error` without internals and stores nothing.
- **No score:** no drift score or severity is computed. Counts only.

## Known limitations

- **Declared state only.** A difference in declared replicas or resources isn't proof that anything
  runs differently.
- **The comparison is only as wide as discovery's coverage.**
  - Comparisons are limited to the formats discovery reads, and to the properties each format can
    declare. Native HCL, Helm and Kustomize templates are unread.
  - Hand-written baseline elements can't be confirmed removed. Matching them needs identity mappings.
- **Synchronous and on request.** There is no scheduling, no continuous monitoring and no alerting.
- **Impact is context only.** It links stored analyses; it doesn't recompute them.
- **Two comparisons coexist.** Discovery's own `GET …/baseline-comparison` (a run's proposal against
  a revision, by id) remains. Drift analysis is the stored, coverage-aware comparison with review.
- **No authorization history.** Nothing tells drift whether a change was authorized.

## Adding a comparison rule

1. Put the rule in `engines/drift`: `comparison.py` for a property or structure, `matching.py` for
   identity, `classification.py` for certainty.
2. Keep the rule deterministic and bounded. It reads only the inputs it is given.
3. If a source type can declare a new property, extend `DECLARABLE` and the discovery mapping
   together.
4. Give each finding its evidence, locations and limitations. Never compare what a source can't
   express.
5. Bump the rule's version in its `RULE` constant. Analyses record it, and the version is part of the
   result's fingerprint.
6. Add unit tests next to the module's existing tests. Add a fixture in
   `tests/unit/drift/test_drift_fixtures.py` when it changes end-to-end behavior, and map it in the
   traceability test.

## Tests

```bash
uv run pytest tests/unit/drift                          # rules, domain, review, 18 fixtures, service
uv run pytest tests/integration/api/test_drift.py      # API against PostgreSQL
uv run pytest tests/security/test_drift_safety.py tests/security/test_traceability_drift_engine.py
make lint typecheck test-unit test-integration test-security test-eval migrate-check
```

The integration and security tests need the database (`make db-up`).

## Example: a confirmed component addition is detected

The baseline is accepted from `compose.yaml` declaring `db`. A later run reads a `compose.yaml` that
also declares `ledger`. The result is one finding:

```json
{"type": "component_added", "classification": "confirmed", "subject": "node:compose:shop/service/ledger",
 "match": "unmatched", "locations": ["compose.yaml#0:services.ledger"], "evidence": ["dsf_…"]}
```

It opens an `open` drift item. (Fixture 2.)

## Example: a configuration change is detected with source evidence

The later run declares `deploy: {replicas: 3}` for `db`:

```json
{"type": "resource_changed", "classification": "confirmed", "subject": "node:compose:shop/service/db",
 "path": "configuration.replicas", "baselineValue": null, "discoveredValue": 3, "match": "same_id",
 "locations": ["compose.yaml#0:services.db"],
 "impact": [{"engine": "capacity", "state": "missing", "analysisId": null}]}
```

The impact entry names capacity because the PostgreSQL specification lists it as reading replicas. No
capacity analysis of the baseline is stored, so the state is `missing`. (Fixtures 5 and 15.)

## Example: incomplete discovery coverage prevents a removal claim

The baseline came from `compose.yaml` and `billing.yaml`. The later run is given only `compose.yaml`.
The result:

- `ledger` (from `billing.yaml`) is `component_removed` with classification **`unknown`**;
- `artifact:billing.yaml` is `coverage_changed`, with classification `unknown`;
- the status is `completed_with_warnings`.

If `billing.yaml` had been supplied but read only in part, the removal would be `potential`. Only a
completely read `billing.yaml` without `ledger` makes it `confirmed`. (Fixtures 3, 4 and 12.)

## Example: an incompatible parser version prevents reliable comparison

The later run was read by Docker Compose extractor version 2, and the baseline's run by version 1:

- `extractor_versions` is `incompatible`, because Compose is the only source type;
- the status is `incompatible_inputs`;
- the only finding is `comparison_incompatible` (`not_comparable`).

The added `ledger` is not claimed. With a second, unchanged source type, only Compose's differences
would be `not_comparable`. (Fixture 10.)

## Example: a finding is acknowledged without changing the architecture

`POST …/drift-items/{id}/review {"action": "acknowledge"}` returns the item with status
`acknowledged` and a new history event. `note`, `link` (to revision 1) and `accept` work the same
way. After all four:

- the architecture's revisions and content hashes are unchanged;
- the discovery run is unchanged;
- five `drift_item.reviewed` audit events exist, recording no note text.

(Fixture 18.)

## Example: a finding is resolved by a later discovery run

A run without `ledger` is analyzed. `POST …/review {"action": "resolve", "evidenceAnalysisId": …}` is
accepted because that analysis compared the same architecture, read `compose.yaml` completely and no
longer detects the item. Naming the analysis that still detects the item is refused with
`still_detected`. If a later analysis detects the difference again, the item becomes `reopened`, with
history `detected, resolve, detected`. (Fixture 17.)

## Example: the baseline architecture remains unchanged throughout analysis

Running analyses, correlating items, reviewing them and confirming identity mappings all leave the
architecture unchanged:

- No architecture revision is written.
- The drift code reads architectures and discovery runs and writes neither; a test enforces this.
- The integration tests compare the revisions' content hashes before and after.
- Accepting drift doesn't update the baseline. Changing the architecture is a separate, explicit
  architecture edit, which can be linked to the item afterwards.

## Repository audit

The Phase 0 audit found no backend drift code: no engine, domain, table, route, permission or test.
The web app has a mock (`apps/web/api/drift.ts` and `apps/web/schemas/drift.ts`) built around a
connected source with expected and actual strings and a severity. The frontend contract describes the
alignment.

Reused:

- stored discovery runs: entities and relationships with stable keys, extractor versions, artifact
  statuses, fingerprints and acceptances;
- the provenance accepted discovery leaves on IR elements;
- the IR diff and its secret redaction;
- immutable revisions with content hashes;
- `load_evidence` for stored downstream analyses;
- the catalog's per-property engine lists;
- the discovery validators;
- the generic `from_dict` deserializer.

## Final review

- **Canonical architecture is never mutated.** A test enforces that drift only reads architectures,
  integration tests compare the revisions before and after, and there are no write calls.
- **No arbitrary score.** Counts only, enforced by a test.
- **No unsupported removal.** A removal is confirmed only with complete coverage of its artifact.
- **Bounded work.** Inputs are bounded by discovery's limits, plus at most 500 exclusions, 10,000
  findings and 1,000 history events per item. No graph traversal goes beyond direct matching.
- **Exceptions:** engine refusals are stored as failed analyses, and unexpected errors return 500
  without internals.
- **Evidence:** impact is linked, never fabricated, and stays missing when no analysis exists.
