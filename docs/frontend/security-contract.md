# Frontend contract: security

The backend implements [docs/api/security.md](../api/security.md). The web app still uses the
shapes it proposed (`apps/web/api/security.ts`, `apps/web/schemas/security.ts`,
`apps/web/features/security/`, the inspector's `SecurityTab`), served by its mock API; the frontend
alignment step adopts the backend's. Decided in [ADR-015](../adr/ADR-015-deterministic-security.md):

| Area | `apps/web` today | Backend | Resolution in the frontend step |
|---|---|---|---|
| Scope | One analysis per project: `GET /projects/{id}/security`, `POST …/security/analyze` | Analyses per architecture: `POST/GET /projects/{id}/architectures/{architectureId}/security-analyses`, `GET …/{analysisId}`, `…/components`, `…/findings` | Analyze the selected architecture; show its latest analysis |
| Input | None | `revision`, `scope`, `analyzers`, `assumptions`, `label`; the policy is the project's (`PUT …/architecture-policy`) | A run form; security properties are edited on the architecture, policy on the project |
| Score | `score: 0–100` | None, by design | Remove the score; show counts by severity, basis and category (`summary`) and the status |
| Trust boundaries | `trustBoundaries[{id, name, nodeIds}]` | `trustZones[{boundaryId, trustLevel (null: not modeled), nodeIds}]` | Show the level or "not modeled" |
| Exposure | `exposure[{nodeId, level, reason}]` | Components' declared `exposure` (null: not modeled); exposure findings with the exact path from a public entry | Show "not modeled" for null; link reachability findings to their path |
| Controls | `controls[{nodeId, authentication: bool\|null, …, handlesPii}]` | `GET …/components`: declared facts as evidence (mechanism names, not booleans), `coverage`, `missing`, `sensitive` | Show the declared value or "not modeled"; `handlesPii` becomes `sensitive` (null when not established) |
| Threats | `threats[{id, title, category (STRIDE), severity, nodeIds, mitigation, evidenceId}]` | Findings of type `threat_candidate` with `threat` (STRIDE), evidence (`threat.derived_from` names the findings), assumptions and mitigations for review | Filter findings by `threat`; say "candidate, not a demonstrated attack" |
| Findings | None | Every finding with `basis` (control gap, potential risk, violation, not evaluable), `certainty`, evidence (secrets `[redacted]`), `missing`, and for requirement and policy findings the `checkKey` they report | A findings view grouped by basis; never label unknowns as passed or failed |
| Requirements and policy | None | `checks[]` with verdicts, the mapping used, `requirementId`/`policyRule` | A checks view; `not_verifiable` is not a pass |
| Version | `architectureVersion`, `analyzedAt` | `revision`, `revisionContentHash`, `completedAt`, `resultFingerprint` | Show the revision analyzed |

Always shown: "Architecture-level analysis of what is modeled; it does not prove the absence of
vulnerabilities." Unchanged and aligned: authentication, the error envelope, severities
(validation's scale), and project scoping (`404 project_not_found` outside the organization).
