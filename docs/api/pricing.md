# Pricing API

An organization's **pricing snapshots**: immutable price lists that its projects' cost analyses
price with. ArchitectOS ships no prices and fetches none from providers: every price is entered or
imported by the organization, with its provenance. A snapshot never changes; a new price list is a
new snapshot, so the prices behind a historical cost analysis stay exactly as they were.

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /organizations/{orgId}/pricing-snapshots` | `pricing.manage` (owners, admins) | `{name, description?, records: [...]}` | `201 PricingSnapshot` | `413 payload_too_large` (2 MiB), `422 invalid_pricing_record, invalid_pricing_snapshot, invalid_money, validation_error`, `429 rate_limited` |
| `GET /organizations/{orgId}/pricing-snapshots` | `organization.read` | | `200 {snapshots, nextCursor}` | `422 invalid_cursor` |
| `GET /organizations/{orgId}/pricing-snapshots/{snapshotId}` | `organization.read` | | `200 PricingSnapshot` | `404 pricing_snapshot_not_found` |
| `GET /organizations/{orgId}/pricing-snapshots/{snapshotId}/records` | `organization.read` | | `200 {records, nextCursor}` | `404 pricing_snapshot_not_found`, `422 invalid_cursor` |

`404 organization_not_found` outside your organizations; a snapshot of another organization is
`pricing_snapshot_not_found` (existence is never revealed). Rate limit: 30 snapshots per user per
hour. At most 10,000 records per snapshot.

## Pricing record

```json
{
  "id": "rds-r6g-large-euw1", "provider": "aws", "service": "rds", "sku": "db.r6g.large",
  "region": "eu-west-1", "currency": "USD", "unit": "instance_hour", "model": "per_unit",
  "unitPrice": "0.26", "per": 1, "effectiveFrom": "2026-09-01", "effectiveTo": null,
  "source": "provider_import", "retrievedAt": "2026-09-20T08:00:00Z",
  "conditions": ["on_demand", "single_az"], "description": "On-demand, single AZ"
}
```

- `id`: unique within the snapshot. `provider`, `service`, `conditions`: lower-case identifiers;
  `sku`: the provider's identifier, compared exactly (never interpreted); `region`: e.g.
  `eu-west-1` or `global`; `currency`: ISO 4217 (prices are never converted).
- `unit`: `month`, `hour` (fixed recurring charges), `instance_hour`, `vcpu_hour`, `gb_hour`,
  `gb_month` (storage), `gb` (transfer), `request`, `operation`, `event`; sizes are decimal
  (1 GB = 10^9 bytes). `per`: the price is per this many units (e.g. `1000000` requests).
- `model`: `fixed` (a recurring `unitPrice` per `month` or `hour`), `per_unit` (`unitPrice` per
  `per` units), `tiered` (`tiers: [{upTo, unitPrice}, …, {upTo: null, unitPrice}]`, graduated per
  billing month: each tier's price applies to the usage within it).
- `effectiveFrom` (required), `effectiveTo` (exclusive, optional); `source`: `user_input`,
  `provider_import` or `catalog`; `retrievedAt` (timezone-aware): when the price was read. A price
  retrieved more than 90 days before the analysis's pricing date, or at an unknown time, is
  reported as stale.

Invalid records are refused as a whole (`invalid_pricing_record`, `details: {field, reason}`):
negative prices, a unit that does not fit the model, tiers not ascending, an open tier that is not
the last, invalid identifiers, currencies or dates.

## How prices are looked up

Exact on provider, service and SKU, then region, then currency, then the pricing date, then the
required conditions. The latest effective record wins; two records effective from the same latest
date are ambiguous and neither is used. A price in another region or currency is reported, never
substituted; a missing price is reported, never zero. See
[the cost engine](../architecture/cost-engine.md).

## Audit

`pricing_snapshot.created` (snapshot id, record count, content hash; never prices), in
`GET /organizations/{orgId}/audit-log`.
