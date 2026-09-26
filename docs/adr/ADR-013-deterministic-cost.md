# ADR-013: Deterministic cost: organization price lists, declared mappings, capacity-sourced usage

- Status: accepted
- Date: 2026-09-26

## Context

Milestone 8 adds cost estimation. Every cost file in the repository was an empty placeholder, there
was no price anywhere, and no provider integration. Prices change, differ by region, currency,
conditions and date, and cannot be fabricated. The Architecture IR described replicas, instance
classes, regions and storage, but not which price applies to a component; the capacity engine
stores demand, bandwidth, storage and scaling per revision. The web app's proposed contract assumed
one project-level estimate with a single total.

## Decision

- **Prices are the organization's, immutable and cited.** Organizations create pricing snapshots
  (records with provider, service, SKU, region, currency, unit, pricing model, effective period,
  source and retrieval time). A snapshot never changes; every analysis names the snapshot and its
  content hash. No prices ship with the product and none are fetched.
- **Lookup is exact.** Provider, service and SKU, region, currency, effective date, conditions,
  latest effective wins, ties are ambiguous, units must match. Nothing is substituted or converted.
- **Mapping is declared.** Optional IR properties (`pricing_service`, `pricing_sku`,
  `pricing_storage_sku`, `pricing_conditions`) with provenance; the instance class is compared
  exactly when no SKU is given; the provider is the project's; nothing is inferred from names.
- **Usage comes from a stored capacity analysis** of the same revision (same content), never
  recomputed; scenarios re-run the capacity engine on that analysis's request and are refused if
  its baseline no longer reproduces.
- **Unknown is never zero.** Unknown lines carry what they miss; totals add priced lines only and
  are a lower bound while anything is unknown or unsupported.
- **Exact money, stated conventions.** Decimal arithmetic (34 digits, 12 places kept, half-even,
  display rounding only); 730 hours a month, 24 a day, 8760 a year; one currency per analysis, no
  conversion.
- **Models in code, a generic framework.** Cost models say what to bill; one framework looks
  prices up and charges them, so pricing rules cannot drift between models.
- **Synchronous, stored, lock-free calculation**, like capacity: read and authorize, calculate on a
  worker thread with no transaction open, store append-only under a re-checked project lock.

## Consequences

- An architecture is priced only as far as it is mapped and priced: organizations maintain price
  lists and architects declare mappings; the results say precisely what is missing.
- Provider integrations (price import) can later produce snapshots (`source: provider_import`)
  without changing the engine or the contract.
- Estimates are not invoices: discounts, commitments, taxes and minimums exist only where a price
  record states them.
- The web app's single project-level estimate is replaced by per-architecture analyses
  (docs/frontend/cost-contract.md).
