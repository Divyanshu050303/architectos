# Frontend contract: discovery

The backend implements [docs/api/discovery.md](../api/discovery.md). The web app still uses the shape
it proposed, served by its mock API:
- `apps/web/api/discovery.ts` (`GET /discovery/connectors`, `POST /projects/{id}/discoveries`,
  `GET /discoveries/{runId}`, `POST /discoveries/{runId}/save`);
- `apps/web/schemas/discovery.ts` (`DiscoveryConnectorSchema`, `DiscoveryRunSchema`,
  `DiscoveredResourceSchema`), `apps/web/types/discovery.ts`;
- `apps/web/api/drift.ts` (`POST /projects/{id}/drift/check`, `422 discovery_required`), which
  assumes a connected source.

The frontend alignment step adopts the backend's shapes. Decided in
[ADR-021](../adr/ADR-021-deterministic-discovery.md):

| Area | `apps/web` today | Backend | Resolution in the frontend step |
|---|---|---|---|
| Input | Connectors (`aws`, `kubernetes`, `terraform`) with `connected` status, region or context options | Supplied artifacts, inline: `{artifacts: [{path, content}], sourceType?, baseline?, label?}`; no connectors, no credentials | Replace the connector picker with file selection (read in the browser, sent as text); explain that rendered output is needed (Helm/Kustomize, `terraform show -json`) |
| Run | Background job (`202`, `steps`, job status) | Synchronous `201` with the stored run: `completed`, `completed_with_warnings` or `failed` (`error.code`) | Show the result directly; no polling |
| Resources | `resources[]` with `status` mapped/unmapped/ignored and `mappedNodeType` | `entities[]` with `role`, `kind` (null: unknown), `mapping` (`exact_match`, `mapped`, `ambiguous` with `candidates`, `unmapped`, `unsupported`, `reason`), `configuration` (value as written, conversion, problem); `relationships[]`; `diagnostics[]`; findings via `…/findings` | Show unknown kinds and ambiguous mappings as things to decide; never default a kind |
| Summary | `total`, `mapped`, `unmapped`, `ignored` | `summary` counts (artifacts, mappings, relationships, diagnostics, validation, elements, unresolved); no score | Show counts; no percentage or confidence |
| Proposal | `proposedArchitecture` (an architecture) | `GET …/proposal`: IR form, `contentHash`, `elements[]` (`included` with `origin`, `needs_review`, `excluded` with `reason`), `validation[]`, `acceptanceProblem` — not an architecture | Render the proposal as a draft; mark inferred and reviewer-stated elements; list what needs review |
| Review | Ignore a resource | `POST …/decisions` per entity or relationship: accept, reject, ignore; `componentId` among candidates; `nodeKind` / `connectionKind` only where the source is silent | Per-candidate controls; refuse overriding stated kinds in the UI too |
| Save | `POST /discoveries/{runId}/save {baseVersion | null}` → Architecture | `POST …/accept {proposalContentHash, architectureId?, baseVersion?, name?}` → `{architectureId, revision, contentHash, createdArchitecture, createdRevision}`; `409 discovery_proposal_not_acceptable` (`proposal_changed`), `409 architecture_version_conflict` | Send the hash of the proposal shown; on conflict reload the proposal or the architecture |
| History | None | `GET /projects/{id}/discovery-runs` (cursor), `GET …/{runId}`, `DELETE …/{runId}` (`409 accepted_discovery_run`) | A run list per project |
| Drift | `POST /projects/{id}/drift/check` with a connected source | Implemented separately: drift analyses of a revision against a stored run — see [drift-contract.md](drift-contract.md). Discovery keeps `GET …/comparison?with=` (run vs run) and `GET …/baseline-comparison` (run vs revision); `not_in_sources` is unknown, never removed | Follow the drift contract for drift; offer the discovery comparisons as previews |
| Permissions | — | `architecture.discover` (members and up) runs, decides, accepts, deletes; viewers read | Hide write controls for viewers |

Always shown: "Discovery reads what the artifacts declare — a desired state — not the running system.
The proposal changes nothing until you accept it." Unchanged and aligned: authentication, the error
envelope, project scoping (`404 project_not_found` outside the organization) and camelCase JSON.
