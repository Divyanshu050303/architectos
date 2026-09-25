# Requirements

Code: `core/domain/requirements/` — `value_objects.py` (shape of values), `requirements.py`
(taxonomy, metric catalog, lifecycle), `normalization.py`, `analysis.py`, `entities.py`,
`requirement_sets.py`, `planning.py`. API: [docs/api/requirements.md](../api/requirements.md).
Decisions: [ADR-007](../adr/ADR-007-requirement-versioning-and-sets.md).

Requirements are **structured**, not text fields: future engines (capacity, validation, simulation,
cost, reliability) consume them. Validation is deterministic and authoritative; an LLM may later help
*interpret* text, never validate it.

## Model

| Field | Meaning |
|---|---|
| `number` / `reference` | `REQ-12`: unique in the project, never reused |
| `type` | the engineering concern (closed list, below); fixed at creation |
| `category` | lower_snake identifier; open vocabulary, closed for the types the engines compute on |
| `title`, `statement` | 1–200 and 1–5,000 characters, no control characters (statement keeps line breaks) |
| `priority` | `critical`, `high`, `medium`, `low`: how much it matters |
| `status` | lifecycle, below |
| `source` | `user` or `ai`; fixed at creation |
| `confidence` | 0–1, 3 decimals, **AI only**: confidence in the *interpretation* ("2k rps" really means ≥ 2000 requests/second), not the probability that the requirement is true, and unrelated to priority |
| `structured_data` | at most one constraint (below), or none |
| `version` | current version; every change appends one |

## Taxonomy

| Type | Known categories | Categories closed? | Constraint required in force? |
|---|---|---|---|
| `capacity` | throughput, requests_per_second, concurrent_users, daily_active_users, storage | yes | yes |
| `performance` | latency, throughput | yes | yes |
| `availability` | availability | yes | yes |
| `reliability` | availability, durability, rpo, rto | yes | yes |
| `data` | retention, consistency, durability, storage | yes | no |
| `cost` | budget | yes | yes |
| `functional` | user, order, payment, notification, search, reporting | no | no |
| `non_functional` | usability, maintainability, portability, accessibility | no | no |
| `security` | encryption, authentication, authorization, pii | no | no |
| `compliance` | retention, data_residency, gdpr, hipaa, pci_dss, soc2 | no | no |
| `operational` | regions, deployment, monitoring, backup | no | no |

"Closed" categories must be one of the known ones; for open types they are suggestions and any
well-formed category is accepted. New categories need no migration (the database checks the format
only).

## Structured constraints

A **quantity**: `{metric, operator, value, unit, percentile?}`, e.g. latency ≤ 300 ms at p95. A
**set**: `{metric, operator: "in", values}`, e.g. regions in {eu-central-1, eu-west-1} (stored sorted).
Unknown keys are refused: structured data is data, never code, and never an untyped bag.

| Metric | Types | Categories | Unit dimension | Operators | Bounds (canonical) |
|---|---|---|---|---|---|
| `requests_per_second` | capacity, performance | throughput, requests_per_second | rate | `>= > <= <` | > 0 |
| `concurrent_users`, `daily_active_users` | capacity | same name | count (whole) | `>= > <= <` | > 0 |
| `storage` | capacity, data | storage | data size | `>= > <= <` | > 0 |
| `latency` | performance | latency | duration | `<= <` | > 0; percentile 0 < p ≤ 100 |
| `availability` | availability, reliability | availability | ratio | `>= >` | 0 < v ≤ 1 |
| `durability` | data, reliability | durability | ratio | `>= >` | 0 < v ≤ 1 |
| `rpo`, `rto` | reliability | same name | duration | `<= <` | ≥ 0 |
| `retention` | data, compliance | retention | duration | `>= > <= <` | > 0 |
| `monthly_budget` | cost | budget | money per month | `<= <` | ≥ 0 |
| `regions` | operational, compliance | regions, data_residency | set | `in` | 1–50 region ids |

Also refused: bounds nothing can satisfy (`rpo < 0`, `availability > 100 %`), a percentile on
anything but latency, and numbers that are not exact decimals (NaN, infinity, more than 9 decimal
places, magnitude ≥ 10¹⁵).

### Units

Explicit and closed; case-sensitive where case carries meaning. Conversion to the canonical unit is
exact multiplication or division:

| Dimension | Units (canonical first) |
|---|---|
| rate | `requests/second`, `requests/minute`, `requests/hour`, `requests/day` |
| count | `users` |
| duration | `ms`, `s`, `min`, `h`, `d` |
| ratio | `ratio` (0.999), `%` (99.9 % = 0.999) |
| data size | `B`, `KB`, `MB`, `GB`, `TB`, `PB` (decimal: 1 KB = 1,000 B) |
| money | `<ISO 4217>/month`, e.g. `USD/month` (never converted between currencies) |

Months and years (variable length) and binary sizes (KiB) are deliberately not units.

## Normalization

Input is rewritten deterministically into the strict stored form, then parsed strictly:

| Input | Stored |
|---|---|
| `{"quantity": "2k requests/sec"}` | value `2000`, unit `requests/second` |
| `"300 ms"`, `"99.9%"`, `"10M users"` | `300 ms`, `99.9 %` (canonical ratio 0.999), `10000000 users` |
| value `"2k"`, `"1.5M"`, `"2bn"`, `"2,000"` | `2000`, `1500000`, `2000000000`, `2000` |
| unit `rps`, `req/s`, `requests per second` | `requests/second` |
| unit `seconds`, `percent`, `usd/month` | `s`, `%`, `USD/month` |
| percentile `"p95"`; metric `rps`, `dau` | `95`; `requests_per_second`, `daily_active_users` |

Refused as **ambiguous** (`ambiguous_unit`), never guessed: lower-case `m` (milli, million or
minutes?), `b` or `B` as a magnitude (billion or bytes?), lower-case data sizes like `gb` (bits or
bytes?), `$` (which dollar?), decimal commas (`2,5`), and magnitude suffixes not touching the number
(`2 k`). Normalization is idempotent.

Numbers are `Decimal`, never float; a float from a JSON client goes through its shortest repr
(`99.9` stays `99.9`), but clients should send decimals as strings. The **canonical form**
(`normalizedData`) converts to the canonical unit: exact where the conversion terminates, otherwise
rounded half-even to 9 places for display (1000 requests/minute = 16.666666667 requests/second);
comparisons always use exact fractions.

## Status lifecycle

```
draft ──> active ──> satisfied ──> active (reopened)
  │         ├──> invalid ──> draft
  └─────────┴──────┴──> deprecated (final)        (satisfied ──> deprecated too)
```

- **In force** = active or satisfied. Changing a requirement in force needs a change reason, and
  measurable types in force need a constraint (a draft may be incomplete).
- Satisfied requirements are **reopened** (set to active, possibly in the same change) before their
  content changes: they were checked against the old content. Deprecated requirements never change.
- AI-sourced requirements always start as drafts: a person promotes them.
- Deleting a requirement is a soft delete; its versions stay (requirement sets may pin them).

## Versioning

- Every change — content or status — appends an immutable **version**: the full state plus who made
  it, when and why (`change_reason`). The database rejects any UPDATE, DELETE or TRUNCATE on versions,
  and guarantees the current state always exists as a version.
- Changes carry the version they were made against (`expectedVersion`); a stale change is a conflict,
  never a silent overwrite.
- Stored history is loaded as it is, **never re-validated**: tightening a rule cannot make old
  history unreadable. `validate` reports requirements that no longer pass today's rules.

## Analysis

Over draft, active and satisfied requirements, deterministic and ordered by requirement number;
every finding names the exact versions.

- **Conflicts** are mathematical impossibilities only: no value satisfies both requirements (a lower
  bound above an upper bound, or equal when either is strict; disjoint region sets). Only like is
  compared with like: same metric, same percentile, same canonical unit (latency p95 is not compared
  with p99; USD is not compared with EUR), after conversion (600,000 requests/minute conflicts with
  ≤ 5,000 requests/second). Tighter bounds in the same direction are not conflicts, and **tensions**
  (99.99 % availability on a $100 budget) are not reported.
- **Completeness**: which of six common concerns are covered — traffic (requests, concurrent or
  daily users), latency, availability, data (data type or storage), security (security type),
  retention — and which are missing. Missing concerns are warnings.
- **Ambiguous**: measurable but without a constraint; latency without a percentile; AI confidence
  below 0.7.
- **Unbounded**: sizing metrics (requests, users, storage) with only upper bounds: nothing to design
  for.

## The architecture boundary

```
Requirements ──> Requirement Set ──> Architecture Planning Input ──> Architecture IR
```

A **requirement set** is an immutable, numbered snapshot (v1, v2, …) of a project's requirements:
exactly which versions an architecture is planned or evaluated against. Only valid requirements in
force can be pinned (drafts are not authoritative), and a set with conflicts is refused. Later
requirement changes never alter a set, so "Architecture v7 was evaluated against Requirement Set v3"
stays explainable.

The **Architecture Planning Input** (`planning.py`, schema version 1) is the only thing engines read:
project id and settings, and each pinned requirement (reference, version, type, category, priority,
status, title, statement, source, confidence and its constraint in canonical units), ordered by
number. It depends on no HTTP, SQL, LLM or cloud code. It is **stored** with the set, not re-derived,
and its SHA-256 over canonical JSON is the set's content hash: equal content, equal hash. Changing its
shape or meaning is a new schema version, never an edit of version 1.
