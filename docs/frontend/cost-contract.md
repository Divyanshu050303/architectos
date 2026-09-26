# Frontend contract: cost

The backend implements [docs/api/cost.md](../api/cost.md) and [docs/api/pricing.md](../api/pricing.md).
The web app still uses the shapes it proposed (`apps/web/api/cost.ts`, `apps/web/schemas/cost.ts`,
`apps/web/features/cost/`), served by its mock API; the frontend alignment step adopts the
backend's. Decided in [ADR-013](../adr/ADR-013-deterministic-cost.md):

| Area | `apps/web` today | Backend | Resolution in the frontend step |
|---|---|---|---|
| Scope | One estimate per project: `GET /projects/{id}/cost`, `POST …/cost/calculate` | Analyses per architecture: `POST/GET /projects/{id}/architectures/{architectureId}/cost-analyses`, `GET …/{analysisId}`, `…/line-items` | Estimate the selected architecture; show its latest analysis |
| Input | None | `snapshotId` (required), `currency`, `pricingDate`, `operatingHoursPerMonth`, `capacityAnalysisId`, `replicasFromCapacity`, `assumptions`, `scenarios` | A form: pick a pricing snapshot and, optionally, a capacity analysis of the same revision |
| Prices | "provider price lists" (implicit) | The organization's pricing snapshots: `…/organizations/{orgId}/pricing-snapshots` | Manage and pick snapshots (owners and admins create them) |
| Total | `total: number` | `totals.{hourly,monthly,annual}` as `{amount, currency, display}` decimal strings, `unknownItems`, `unsupported`, `complete` | Show `display`; when `complete` is false say "at least" and list the unknown items |
| Per node | `nodes[{nodeId, monthly, breakdown[{item, monthly}]}]` | `summary.byComponent[]` (shares, counts) and `GET …/line-items` (quantity, unit, unit price, `monthly` or `null`, price record, assumptions, `missing`) | Show "unknown" for `null`, never 0; show what is missing |
| Categories | `byCategory[{category, monthly}]` | `summary.byCategory[]`, plus `byResource`, `byProvider`, `byRegion`, `byKind`, `bySensitivity` | Parse decimals; group `null` keys as "not priced" |
| Assumptions | `[{id, statement}]` | `assumptions[]` (periods, hours, snapshot, currency, workload) and `limitations[]` | Show both; always show "estimate, not an invoice" |
| Evidence | `evidenceIds` | Each line's `price` record, model and version, `assumptions`; `inputs` of the analysis | Show where each amount comes from and how fresh its price is (`price_stale`) |
| Scenarios | None | `scenarios[]`: periods, comparison (difference, `percentage` or `percentageUndefined`, changed lines and assumptions, unknown differences) | A comparison view; no percentage when undefined |
| Drivers | None | `summary.drivers` (largest component and category, top items, fixed, usage, workload-sensitive) | Show the drivers; no "waste" labels |
| Version | `architectureVersion` | `revision`, `revisionContentHash`, `snapshotId`, `resultFingerprint` | Show the revision and snapshot priced |

Unchanged and aligned: authentication, the error envelope, and project scoping (`404
project_not_found` outside the organization).
