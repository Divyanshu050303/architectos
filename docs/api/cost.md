# Cost API

Deterministic cost estimates of an architecture revision, priced with one of the organization's
[pricing snapshots](pricing.md). The same revision, snapshot, request, capacity analysis and
scenarios always give the same result and `resultFingerprint`. An estimate, not an invoice: every
amount traces to a price record, nothing is measured or billed, and what cannot be priced is
reported as unknown, never 0. See [the cost engine](../architecture/cost-engine.md).

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /projects/{projectId}/architectures/{architectureId}/cost-analyses` | `architecture.analyze` | `{snapshotId, revision?, currency?, pricingDate?, operatingHoursPerMonth?, capacityAnalysisId?, replicasFromCapacity?, assumptions?, label?, scenarios?}` | `201 CostAnalysis` | `404 architecture_revision_not_found, pricing_snapshot_not_found, capacity_analysis_not_found`, `409 project_archived, architecture_archived`, `422 invalid_cost_request, incompatible_capacity_analysis, invalid_capacity_scenario, invalid_capacity_quantity, invalid_money, validation_error`, `429 rate_limited` |
| `GET /projects/{projectId}/architectures/{architectureId}/cost-analyses` | `architecture.read` | | `200 {analyses, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/architectures/{architectureId}/cost-analyses/{analysisId}` | `architecture.read` | | `200 CostAnalysis` | `404 cost_analysis_not_found` |
| `GET /projects/{projectId}/architectures/{architectureId}/cost-analyses/{analysisId}/line-items` | `architecture.read` | | `200 {lineItems, nextCursor}` | `404 cost_analysis_not_found`, `422 invalid_cursor` |
| `GET /cost/models` | signed in | | `200 {models}` | |

On every project endpoint: `404 project_not_found` / `architecture_not_found` outside your
organizations (existence is never revealed) and `403 permission_denied` without the permission.
Viewers read analyses; members, admins and owners also run them. The pricing snapshot is looked up
in the project's organization only and the capacity analysis in the same architecture only: any
other is `404`, like a missing one.

## Running an analysis

Synchronous: priced and stored in the request. Rate limit: 120 per user per hour. Archived projects
and architectures refuse new analyses (`409`).

```json
{
  "snapshotId": "0199...",
  "pricingDate": "2026-09-26",
  "operatingHoursPerMonth": 730,
  "capacityAnalysisId": "0199...",
  "replicasFromCapacity": true,
  "assumptions": [{"key": "on_demand", "statement": "No reserved capacity or discounts."}],
  "scenarios": [
    {"name": "Double", "growth": 2},
    {"name": "Scale out", "changes": [{"elementId": "api", "property": "replicas", "value": 6}]},
    {"name": "Office hours", "operatingHoursPerMonth": 220}
  ]
}
```

- `snapshotId` (required): a pricing snapshot of the project's organization.
- `currency`: default the project's currency. Prices in another currency are never converted: those
  lines are unknown (`missing: ["price.currency"]`).
- `pricingDate`: prices must be effective on this day; default today (UTC).
- `operatingHoursPerMonth`: how long resources run, `0 < h <= 730` (default 730, all month).
- `capacityAnalysisId`: a capacity analysis of the **same architecture and revision** (another
  revision or content: `422 incompatible_capacity_analysis`, `details.reason`: `revision`,
  `revision_content`, `architecture`, `no_result`); usage-priced resources (requests, transfer,
  ingestion, stored volume) take their quantities from it. Without one they are unknown.
- `replicasFromCapacity`: bill the replicas the capacity analysis requires, where a capacity model
  defines scaling (needs `capacityAnalysisId`).
- `scenarios` (at most 10, unique names): a capacity scenario (`growth`, `growthRate` + `periods`,
  or `targetRate`; `changes` to named nodes and connections, e.g. `replicas`, `storage_bytes`) plus,
  optionally, `operatingHoursPerMonth` and `replicasFromCapacity`. Priced with the same snapshot.
  A workload change needs `capacityAnalysisId` (`invalid_cost_request`,
  `field: scenarios.workload`); if the capacity models changed since that analysis, scenarios are
  refused (`incompatible_capacity_analysis`, `reason: capacity_models_changed`).

The provider is the project's `cloudProvider` (without one, nothing can be looked up and the
result says `no_provider`). `invalid_cost_request` carries `details: {field, reason}`.

## The analysis

`status`: `completed` (every line priced, nothing unsupported), `partial` (some lines priced),
`insufficient_pricing` (nothing priced), `unsupported` (no model applies), or `failed` (the engine
could not run: `error.code` `engine_error`; nothing partial is stored).

```json
{
  "id": "0199...", "revision": 3, "status": "partial", "snapshotId": "0199...", "currency": "USD",
  "totals": {
    "currency": "USD",
    "hourly": {"amount": "0.113106849315", "currency": "USD", "display": "0.11"},
    "monthly": {"amount": "82.568", "currency": "USD", "display": "82.57"},
    "annual": {"amount": "990.816", "currency": "USD", "display": "990.82"},
    "unknownItems": 3, "unsupported": 0, "complete": false
  },
  "summary": {
    "knownTotalIsLowerBound": true,
    "byComponent": [{"key": "api", "monthly": {"...": "..."}, "share": "0.72", "pricedItems": 1, "unknownItems": 0, "complete": true}],
    "byResource": [], "byCategory": [], "byProvider": [], "byRegion": [], "byKind": [], "bySensitivity": [],
    "unknown": [{"elementId": "bus", "resource": "requests", "missing": ["capacity.requests_per_month"], "reason": "..."}],
    "drivers": {"largestComponent": {}, "largestCategory": {}, "topItems": [], "fixed": {}, "usage": {}, "workloadSensitive": {}}
  },
  "assumptions": [{"label": "hours_per_month", "value": "730"}, {"label": "uptime", "value": "1"}],
  "scenarios": [{"name": "Double", "status": "completed", "periods": {"hour": {}, "day": {}, "month": {}, "year": {}},
                 "comparison": {"difference": {}, "percentage": "100", "percentageUndefined": null,
                                "changedLines": [], "changedAssumptions": [], "unknownDifferences": []}}],
  "limitations": [{"code": "estimate_not_invoice", "message": "..."}],
  "modelSet": {"version": "…", "models": [{"id": "compute.instances", "version": 1}]},
  "inputs": {"snapshot_id": "…", "currency": "USD", "pricing_date": "2026-09-26", "provider": "aws", "…": "…"}
}
```

- Amounts: exact decimal strings (12 places) with a `display` rounded half-even to the currency's
  minor unit. Periods derive from monthly amounts: 730 h/month, 24 h/day, 8760 h/year.
- **Unknown is never 0**: an unknown line has `monthly: null` and names what it misses; while any
  line is unknown or any component unsupported, `totals.complete` is false and the known total is
  a lower bound.
- `bySensitivity`: `fixed`, `stepwise` (capacity-required replicas), `linear` (usage at a per-unit
  price), `tiered` (usage at a graduated price).
- Scenario comparisons: known totals per period, their difference, `percentage` only when both
  sides are complete and the baseline is above 0 (else `percentageUndefined`: `incomplete` or
  `zero_baseline`), changed lines, changed assumptions, and the lines unknown on either side.

## Line items

`GET …/line-items?component=&status=priced|unknown&category=&cursor=&limit=` (up to 500), by
component then resource. Each: `quantity` per month in `unit`, `unitPrice` per `per` units,
`monthly`, the `price` record (snapshot, provider, service, SKU, region, unit, model, effective
dates, source, retrieval time), the model and version, `assumptions` (mapping, instances, operating
hours, capacity provenance, price freshness), and for unknown lines `missing` and `reason`.

## Audit

`architecture.cost_analyzed` (analysis id, revision, status, snapshot id, line item, unknown and
unsupported counts; never amounts or prices), in `GET /organizations/{orgId}/audit-log`.
