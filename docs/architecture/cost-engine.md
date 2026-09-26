# Cost engine

Deterministic cost estimates of an architecture revision. Estimates are **not invoices and not
measurements**: every amount is a declared quantity times a price the organization entered or
imported, and what cannot be established is reported as unknown, never as 0. No language model
takes part; the same inputs always give the same result and `resultFingerprint`.

Code: `core/domain/cost/` (contracts, pricing, lookup, results, aggregation, projection, capacity
basis, services), `engines/cost/` (framework, mapping, usage, models, projection, engine adapter),
`persistence/` (migrations 0013 and 0014), `apps/api/routes/{pricing,cost}.py`. API:
[pricing](../api/pricing.md), [cost analyses](../api/cost.md). Decision:
[ADR-013](../adr/ADR-013-deterministic-cost.md).

## Responsibilities

- Keep an organization's **pricing snapshots**: immutable, provenance-carrying price lists.
- **Map** architecture components to prices from their own configuration, never by guessing.
- **Price** each billed resource: quantity × price, in exact decimals, with the price record, the
  model and the assumptions behind it.
- Take **usage** (requests, transfer, ingestion, stored volume, required replicas) from a stored
  capacity analysis of the same revision; never recompute capacity.
- **Aggregate** into totals, breakdowns and drivers without losing line-item traceability, keeping
  unknown items apart.
- **Project** billing periods and **compare** scenarios, recalculated, never extrapolated.
- Store every analysis, append-only, with what it used.

Not responsibilities: fetching prices from providers, billing, invoices, budgets, currency
conversion, discounts or commitments beyond what a price record states.

## Supported pricing models

A pricing record's `model`:

| Model | Units | Charge for a quantity `q` |
|---|---|---|
| `fixed` | `month`, `hour` | `q / per × unit_price`, `q` in periods (1 month; operating hours) |
| `per_unit` | `instance_hour`, `vcpu_hour`, `gb_hour`, `gb_month`, `gb`, `request`, `operation`, `event` | `q / per × unit_price` (`per`: e.g. 1,000,000 requests) |
| `tiered` | as `per_unit` | graduated per billing month: each tier's price applies to the part of `q` within it (`up_to` inclusive, the last tier open) |

Sizes are decimal (1 GB = 10^9 bytes). Planned, not implemented: volume (all-units) tiers,
commitments and reservations as pricing models, minimum charges, free tiers other than a tier
priced 0.

## Pricing catalog structure

There is no shipped catalog: ArchitectOS contains no prices. An organization creates snapshots
(`pricing.manage`) of up to 10,000 records. A record: `id`, `provider`, `service`, `sku`,
`region`, `currency`, `unit`, `model`, `unit_price` or `tiers`, `per`, `effective_from`,
`effective_to` (exclusive), `source` (`user_input`, `provider_import`, `catalog`),
`retrieved_at`, `conditions` (e.g. `on_demand`), `description`. Validation refuses negative prices,
units that do not fit the model, unordered tiers and malformed identifiers
(`core/domain/cost/pricing.py`).

## Pricing snapshot semantics

A snapshot is immutable (append-only tables with triggers); a new price list is a new snapshot, so
the prices behind a stored analysis never change. Its `content_hash` (SHA-256 of its sorted
records) identifies the price list exactly and is part of every result. Snapshots belong to one
organization; any other organization's snapshot is indistinguishable from a missing one.

**Lookup** (`core/domain/cost/lookup.py`), in order, each step ending the lookup when nothing is
left: same provider, service and SKU (`not_found`); same region (`region_mismatch`, the regions that
have it are named, never used); same currency (`currency_mismatch`, never converted); effective on
the pricing date (`not_effective`); carrying every requested condition; the latest effective record
wins, two equally latest are `ambiguous` (none is picked); its unit must be the one the charge
needs (`unit_mismatch`). A per-analysis index groups records by provider, service and SKU.

## Supported providers and services

Providers: the project's `cloudProvider` (`aws`, `gcp`, `azure`) names the price records to use.
There is **no provider integration**: no price import, no service or SKU catalog, no
provider-specific rules (`engines/cost/providers/*` are empty on purpose). Any service and SKU the
organization prices can be used; the engine knows none by name.

## Resource mapping

Everything comes from the architecture (`engines/cost/mapping.py`), and every line says where each
input was read:

- **Service**: the node's `pricing_service`; never inferred from its kind or technology.
- **SKU**: `pricing_sku` (`mapping: explicit`), else `instance_class` compared exactly with the
  snapshot's SKUs (`mapping: instance_class`), else unmapped. Storage has `pricing_storage_sku`.
- **Region**: the node's `region`, else that of the innermost enclosing boundary that declares one.
- **Conditions**: `pricing_conditions`, required on the price.
- **Instances**: `replicas`; autoscaling bounds are not averaged and nothing is defaulted.
- On-premises components (`deployment_model: on_premises`) are unsupported (`on_premises`), not 0.

Registered models (all version 1), one per billed component kind:

| Model | Kinds | Resources |
|---|---|---|
| `compute.instances` | service, worker, gateway | `instances` (instance hours); serverless: `requests` |
| `database.instances` | database, cache | `instances`; database `storage` (GB-month, own SKU); serverless: `requests` |
| `storage.volume` | storage | `storage` (GB-month): the capacity analysis's retained volume, else `storage_bytes` |
| `messaging.brokers` | queue | `instances` and declared `storage`; serverless: `requests` |
| `networking.edge` | load balancer, CDN | load balancer `hours` (one per component unless `replicas`); CDN `data_transfer` (GB) |
| `observability.platform` | observability | managed/serverless: `ingestion` (GB); self-hosted: `instances`, declared `storage` |
| `external.subscription` | external | `subscription` (one month), only when mapped |

Clients and boundaries are not billed.

## Workload and capacity integration

A cost analysis may cite a stored capacity analysis (`capacityAnalysisId`). It must describe the
same architecture, revision number and revision content, and have a result; anything else is
refused (`incompatible_capacity_analysis`), never combined (`core/domain/cost/capacity.py`). Usage
(`engines/cost/usage.py`):

- **Work** (requests, events, operations) reaching the component, grouped by dimension; demand the
  capacity analysis marks incomplete, or mixed dimensions, stay unknown.
- **Transfer** and **ingestion**: the egress and ingress bytes of its `network-bandwidth` estimate.
- **Stored volume**: its `storage-growth` estimate of what the retention keeps.
- **Required replicas**, only with `replicasFromCapacity`: where a capacity model defines horizontal
  scaling (`replica-throughput`); else the declared replicas, said so.

Each usage line records the capacity analysis, its result fingerprint and the capacity model and
version behind the number.

## Calculation formulas

| Quantity | Formula |
|---|---|
| Instance hours | replicas × operating hours per month |
| Fixed hourly | count × operating hours per month |
| Monthly subscription | 1 month |
| Provisioned storage | `storage_bytes` / 10^9 GB-month |
| Sustained ratio | average rate / peak rate (batch: 1; no average rate: unknown) |
| Monthly work | design work per second × sustained ratio × operating hours × 3600 |
| Monthly transfer / ingestion | design bytes per second × sustained ratio × operating hours × 3600 / 10^9 |
| Retained volume | design retained bytes × sustained ratio / 10^9 GB-month |
| Line amount | the price record's charge for the quantity (see pricing models) |

## Currency and unit handling

Money is exact `Decimal`: every calculation runs in a 34-digit context, amounts are kept at 12
decimal places (half-even), and rounding to the currency's minor unit (2, or 0 and 3 by ISO 4217)
happens only for `display`. Amounts must be below 10^15; a charge beyond that is unknown
(`magnitude`), never an error. One analysis has one currency (default the project's): a price in
another currency is unknown, never converted, and amounts of different currencies are never added
(`currency_mismatch`). A charge's unit must equal the price's unit: nothing is converted.

## Billing-period assumptions

Results are monthly. Other periods derive from monthly amounts by hours, by convention: a month is
730 hours (8760 / 12: every month is the same length, about 30.42 days; calendar months are not
modeled), a day 24 hours, a year 8760 hours (365 days). Resources run for the request's operating
hours (0 < h ≤ 730; default 730); `uptime` = hours / 730. Periods are converted, never added across.
Every analysis states these as its `assumptions`.

## Provenance and freshness

Each priced line carries its price record (snapshot, record, provider, service, SKU, region, unit,
model, effective dates, source, retrieval time), the cost model and version, and assumptions:
mapping, instances, operating hours, where provider, SKU and region were read, and capacity
provenance. A price retrieved more than 90 days before the pricing date, or at an unknown time, is
**stale**: it is used, flagged on the line (`price_stale`), and the result says `stale_pricing`.

## Unknown and unsupported costs

- A line whose quantity, mapping, provider or price is not available is **unknown**: no amount
  (never 0), `missing` names what (`configuration.pricing_sku`, `price.region`,
  `capacity.requests_per_month`, `workload.average_rate`, …), `reason` says why.
- A component no model prices (or an on-premises one) is **unsupported** (`no_cost_model`,
  `on_premises`); a model that fails or misbehaves is reported (`model_failed`, `invalid_output`)
  and the other models still count.
- Totals add priced lines only; while anything is unknown or unsupported `totals.complete` is false
  and the known total is a **lower bound** (`knownTotalIsLowerBound`), listed with the unknown
  items. Status: `completed`, `partial`, `insufficient_pricing`, `unsupported`, or `failed`.
- A declared zero (e.g. 0 replicas) is priced at 0: it is known, not missing.

## Aggregation and drivers

Breakdowns by component, resource, category, provider, region, fixed vs usage and workload
sensitivity each add up exactly to the known total (lines without a price group under `null`).
Drivers are calculations: the largest component and category, the largest line items, the fixed,
usage and workload-sensitive amounts. Sensitivity: `fixed` (declared), `stepwise` (capacity-required
replicas), `linear` (usage at a per-unit price), `tiered` (usage at a graduated price). Nothing is
called inefficient or wasteful.

## Scenario projections

A scenario is a capacity scenario (growth, compound growth or a target rate; configuration changes
such as replicas or storage) with, optionally, other operating hours or capacity-required replicas.
It is priced with the same snapshot, currency and pricing date; its usage comes from the capacity
engine's run of the same scenario on the cited analysis's request, whose baseline must reproduce the
stored result exactly (else `capacity_models_changed`). Tiered prices and replica steps are
recalculated, never extrapolated. The comparison gives the known totals per period, their
difference, the percentage only when both sides are complete and the baseline above 0
(`percentageUndefined`: `incomplete`, `zero_baseline`), each changed line, the changed assumptions
and every line unknown on either side. A workload change needs a capacity analysis.

## API contracts

[docs/api/pricing.md](../api/pricing.md) (snapshots) and [docs/api/cost.md](../api/cost.md)
(analyses): run, list, read, line items, `GET /cost/models`. Amounts are decimal strings with a
display value; unknown amounts are `null`.

## Persistence

`pricing_snapshots`, `pricing_records` (0013) and `cost_analyses`, `cost_line_items` (0014), all
append-only (triggers). An analysis stores its inputs (request, provider, scenarios), snapshot and
capacity references, model set, fingerprints, totals, summary, unsupported items, limitations,
assumptions and scenario projections; line items are rows for paging and filtering. Foreign keys:
architecture, revision and capacity analysis in the same project; snapshot in the same
organization. The revision's content is referenced (number and hash), not copied. An analysis is
read and authorized, calculated on a worker thread with no transaction open, then stored under a
re-checked project lock with its audit entry.

## Authorization

Creating snapshots: `pricing.manage` (owners, admins); reading them: `organization.read`. Running a
cost analysis: `architecture.analyze` (members and up) on a modifiable project and architecture;
reading: `architecture.read`. Organization, project and actor come from the path and the session,
never the body; unknown body fields are refused. Lookups go project → architecture → analysis; the
snapshot is looked up in the project's organization, the capacity analysis in the same
architecture. Audit entries (`pricing_snapshot.created`, `architecture.cost_analyzed`) carry ids and
counts, never prices or amounts.

## Determinism

Models run in id order on an immutable context, nodes in id order, lines and every collection
sorted, arithmetic in exact decimals, no clock or random value in a result. The context fingerprint
covers the revision (content hash), the request, the snapshot (content hash), the provider and the
capacity result's fingerprint; reordering snapshot records or architecture elements changes nothing.

## Limits and performance

| Limit | Value |
|---|---|
| Architecture | 1,000 nodes (IR) |
| Pricing records per snapshot | 10,000 (2 MiB request) |
| Scenarios per analysis | 10 (unique names) |
| Assumptions | 50 |
| Line items page | 500 |
| Analyses / snapshots | 120 / 30 per user per hour |

Measured: the engine prices 1,000 components against 10,000 prices in 0.07 s (1.0 s before the
price index). Through the API with storage, 999 components with a capacity analysis: 0.59 s, 22 SQL
statements; with 10 scenarios 3.1 s (mostly the capacity engine's re-runs), still 22 statements,
3.4 MB response. Reads: 0.02–0.15 s; read cost independent of size. No caching.

Risks: an analysis is synchronous (seconds at the limits with scenarios; run on a worker thread,
but CPU-bound work still shares the process); responses with many scenarios are large (every
changed line per scenario).

## Security

No code or expressions from requests, no dynamic imports; every input bounded (sizes, counts,
decimal magnitude and precision); pricing identifiers and SKUs are pattern-checked and used only in
equality comparisons; errors carry fixed messages; audit entries carry ids and counts. Security
sweeps cover every endpoint (authentication, tenant isolation, mass assignment, audit, documentation).

## Known limitations

- No shipped prices, no provider integration, no currency conversion.
- Estimates, not invoices: no discounts, commitments, taxes, support plans, free tiers or minimums
  unless a price record states them.
- Calendar months are not modeled (730 hours by convention).
- Usage assumes the workload's average rate for every operating hour.
- Not priced: backups, I/O, snapshots, storage requests and retrievals, load balancer traffic,
  per-message API calls beyond one request per unit of work, third-party usage fees.
- Only horizontal scaling with a declared per-replica throughput changes billed replicas.
- Scenarios use the baseline's snapshot; comparing snapshots means two analyses.
- A revision comparison (`GET …/compare`) does not carry cost (`cost: null`).

## Adding a pricing model

A pricing model (how a record charges) is a `PricingModel` member with its validation and charge in
`core/domain/cost/pricing.py`, documented here and in the API docs, with tests of each boundary.

A cost model (what a component bills):

1. Write a class with `meta = CostModelMeta(...)` (new id, version 1, kinds, resources, units,
   configuration, usage, limitations) and `charges(node, context)`, building charges with the
   helpers of `engines/cost/mapping.py` (and `usage.py` for usage); return `NotPriced` for what it
   cannot price.
2. Put it in its family's module and list it in `engines/cost/registry.py`.
3. A change to what a model bills is a new `version`.
4. Test the mapped case, each missing input, unit mismatch, zero and huge quantities, determinism.

## Tests

```
make test-unit          # tests/unit/cost
make test-integration   # tests/integration/api/test_{pricing,cost}.py, migrations
make test-security      # sweeps, documentation, traceability (test_traceability_cost_engine.py)
make migrate-check      # migrations 0013, 0014
```

## Repository audit

Before any change (phase 0): `engines/cost/*` (11 files, including `providers/{aws,azure,gcp}.py`),
`ai/agents/cost_agent.py`, `core/domain/components/*` and `knowledge/*.yaml` existed but were empty;
there was no price, no pricing model and no provider integration anywhere. Reused: the IR and its
configuration properties and provenance, project settings (`cloud_provider`, `currency`), the
capacity engine's stored analyses and scenario machinery, the engine patterns of validation and
capacity (registry, orchestrator, engine port, stored immutable analyses, three-step service,
sweeps), project access and locking, audit, rate limits, pagination. Shared result types (evidence,
limitations, unsupported items, model sets) moved to `core/domain/engine_results.py` rather than
being duplicated. Placeholders were filled rather than parallel files created.

## Final review

Two independent reviews (security, correctness). Security: one high finding, fixed — each price
lookup scanned the whole snapshot and the calculation ran on the event loop (now a per-analysis
price index and a worker thread). Correctness: no critical or high issue; ratio divisions ran
outside the money context (now inside). Confirmed sound: tenant scoping in services and foreign
keys, authorization, mass assignment, input bounds, decimal exponent and precision attacks, audit
content, lookup order, pricing models and tier boundaries, unknown-never-zero, determinism, stored
round-trips.
