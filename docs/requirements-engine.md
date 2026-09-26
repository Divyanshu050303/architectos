# Requirements Engine

## Purpose

The Requirements Engine turns a plain-language description of a system ("a food delivery platform,
2,000 rps at peak, p95 under 300 ms, 99.9 % availability, card payments") into structured,
validated engineering requirements, and says precisely what is still missing, vague, assumed or
contradictory before any architecture work starts.

It never decides on its own. The rule throughout:

    the rules (or a language model) propose → the domain validates → the analysis is stored
    → a person chooses → promotion creates draft requirements → people activate them
    → a requirement set pins them → the Architecture Planning Input is built from the set

## Architecture

| Layer | Where | Pure? | Role |
|---|---|---|---|
| Domain | `core/domain/requirements/` | yes (services async, no I/O of their own) | The canonical model: `Requirement` and its versions, taxonomy and metric rules, units and exact normalization, candidates, analyses, conflicts, requirement sets, the planning input; `RequirementAnalysisService` (analyze, get, promote); ports `RequirementsAnalyzer`, `RequirementAnalysisRepository`, `Metrics` |
| Engine | `engines/requirements/` | yes | The pipeline below. Implements `RequirementsAnalyzer`. Imports the domain; the domain never imports it |
| AI | `ai/llm/`, `ai/agents/requirement_agent.py`, `ai/evaluation/` | no (network) | The `StructuredLlm` port and its Anthropic adapter; the extraction agent implementing the engine's `SemanticExtractor` port; the evaluation harness |
| Persistence | `persistence/` | no | `requirement_analyses` (append-only), `requirements.origin_*` columns, repositories |
| API | `apps/api/` | no | Routes, schemas, rate limits, `LogMetrics`, the engine built once per process (`app.state.requirements_engine`) |

Impure work (database, model calls, audit, metrics) happens in the service, the adapters and the
API. Every engine module is a pure function of its inputs, so the same engine runs in an API
request, a worker, the evaluation CLI (`make eval`) or a test without changes. The engine runs
**between** transactions: no lock or connection is held while it (or a model) works.

## Pipeline

    raw text
      → normalizer      quantities, magnitudes ("2K", "10 million"), units, operator phrases
                        ("at least", "under", "between … and …"), percentiles, nines
      → extractor       sentences and sub-clauses, candidates with exact source spans; what
                        cannot be read is kept as Unresolved, never guessed
      → classifier      type, category, metric, scope (a unit decides; else the nearest keyword
                        in the same clause), with a confidence
      → [semantic]      only when the rules left requirement-like text unread (see LLM boundary)
      → validation      every candidate through the domain rules; provenance of every span;
                        duplicates; normalization problems
      → ambiguity       vague language, unclassifiable quantities, incomplete candidates
      → assumptions     every interpretation the engine applied, surfaced
      → conflicts       candidates against each other and against the project's requirements
      → completeness    which areas this kind of system needs, which are covered
      → result          JSON (result_schema 1), stored as is in the analysis

`engines/requirements/service.py` only orchestrates. Each finding has a deterministic key
(`find_…`), a kind, a code, a severity (`blocking`, `warning`, `info`), a message, a suggestion,
the span of text it is about, and the candidates or existing requirements (`REQ-n@vN`) involved.

## Taxonomy

Types: `functional`, `non_functional`, `capacity`, `performance`, `availability`, `reliability`,
`security`, `data`, `compliance`, `operational`, `cost`. Closed types have known categories
(`core/domain/requirements/requirements.py`, `KNOWN_CATEGORIES`); measurable categories have a
metric (`METRICS`): `requests_per_second`, `orders_per_second`, `daily_active_users`,
`monthly_active_users`, `concurrent_users`, `latency`, `availability`, `durability`, `rpo`, `rto`,
`retention`, `storage`, `monthly_budget`, `regions`. Scopes: `system` (default), `service`, `api`,
`database`, `queue`, `user`, `region`, `data`. The full table is in
[docs/domain/requirements.md](domain/requirements.md).

## Canonical model

A requirement has a type, category, title, statement (the user's own words), priority, status,
scope, source, confidence and an optional structured constraint:

- quantity: `{metric, operator, value, unit, percentile?}` with operators `>=`, `>`, `<=`, `<`,
  `==`;
- range: `{metric, operator: "between", min, max, unit, percentile?}`;
- set: `{metric: "regions", operator: "in", values}`.

Sources: `user` (the only source that may start active), `ai` and `discovery` (drafts, with a
required confidence), `imported`, `system` (the deterministic rules). Every change is a new
version (append-only); see [ADR-007](adr/ADR-007-requirement-versioning-and-sets.md).

## Normalization

Values are exact decimals, converted to a canonical unit per dimension (`requests/second`, `ms`,
`ratio`, `B`, days, …) with exact fractions; rounding is for display only. "99.9 %" is `0.999`,
"three nines" is `0.999`, "2K rps" is `2000 requests/second`, "1 year" is `365 days` (an
assumption, surfaced). Anything the normalizer does not recognize stays unread rather than
guessed. Normalization is idempotent.

## Ambiguity

Reported, never resolved by the engine:

- vague language ("fast", "high traffic", "highly available", "global", "cheap", "secure"),
  unless the text quantifies that concern elsewhere; one finding per kind, at its first
  occurrence;
- quantities without a purpose: "10M users" (daily, monthly, registered, concurrent?), "within
  5 minutes" (latency, RTO?), "40 %" (of what?), with the readings offered as `options`;
- incomplete candidates: latency without a percentile, encryption without at rest/in transit,
  low-confidence interpretations.

Ambiguities are warnings or info; whether a gap blocks architecture work is completeness's call.

## Assumptions

Every interpretation applied is surfaced with its reason, a confidence in the assumed reading,
the candidate it affects and how to remove it: an implied operator ("2000 rps" read as at least),
a year as 365 days, a month as 30 days, "$" as USD, a range's unit shared by both ends.
Interpretations that could change the meaning by orders of magnitude are never applied: they stay
ambiguities and no candidate is created.

## Completeness

Contextual: the text is matched against profiles (`payments`, `health`, `internal_tool`,
`public_platform`, `data_ingestion`, `saas`, on top of a baseline), each rating the importance of
areas (`traffic`, `capacity`, `latency`, `availability`, `reliability`, `data`, `security`,
`compliance`, `operational`, `cost`) with a stated reason and the words that triggered it. The
strongest rating wins. Status is `complete`, `incomplete` or `unknown` (nothing to assess); a
missing essential area is blocking. Existing project requirements count as coverage.

## Conflict detection

Like is compared only with like: same metric, scope, percentile and canonical unit. Each pair has
a relation (`disjoint`, `equal`, `first_stricter`, `second_stricter`, `overlap`) over exact
intervals. Disjoint bounds ("at least 5,000 rps" and "at most 2,000 rps") are a **blocking
conflict**; different but compatible bounds are an informational **consistency** finding. Candidates
are compared with each other and with the project's analyzed requirements (drafts and active),
which are referenced as `REQ-n@vN`.

## Confidence

Confidence is how sure the engine (or model) is that it *read the text correctly*, never how
important or how likely the requirement is (that is priority). Rules: 0.95 when a unit decides the
classification, 0.9 by keyword, 0.8 for qualitative requirements, 0.05 less when the operator was
implied. Model proposals carry the model's confidence. Below 0.7 an ambiguity says to check it.
People never state a confidence; `user` requirements have none.

## LLM boundary

- **Off by default.** `REQUIREMENTS_LLM_PROVIDER=none|anthropic`; Anthropic needs
  `ANTHROPIC_API_KEY` (a secret, never logged or returned).
- **Only when needed.** The model is asked only when the rules leave requirement-like text unread
  (`unresolved_text`, `uncovered_sentences`); the reason is recorded in `semantic.reason`.
- **Data, not instructions.** The system prompt is fixed; the user's text is sent separately
  between `<requirements_text>` delimiters, with any delimiter inside it escaped. The output is a
  closed JSON schema (enums, no free-form fields, at most 50 items).
- **Proposals, checked.** Each proposal's quote must appear verbatim in the input (that span
  becomes its provenance); it then goes through the same normalization and validation as
  everything else. Failures are rejected with a reason (`rejected` findings), never repaired.
  Where the rules and the model disagree, the rules win.
- **Never canonical.** A model proposal is a draft candidate with `source: ai`; only a person's
  promotion creates a requirement, and only as a draft.
- **Never required.** Unavailability, timeouts and malformed output become an `extraction` warning;
  the deterministic analysis stands. `engine_version` records `+provider/model#prompt_version` when
  a model's proposals were used.

## Candidate and canonical lifecycle

    analysis (append-only: raw input exactly as written, its SHA-256, engine version, result)
      └ candidates (deterministic key = hash of the interpretation, exact span, method, source)
           └ promote (chosen keys, at most 100): re-validated like any creation → draft requirement
                with origin {analysis_id, candidate_key}

Promotion is idempotent: a unique index on the live origin means a candidate becomes at most one
live requirement; promoting again returns it with `created: false`. Candidates are read back from
the stored result and their keys re-verified, so a tampered result cannot be promoted.

## RequirementSet and versioning

A requirement set pins `(requirement, version)` pairs of valid requirements in force; later edits
never change it, and conflicting requirements cannot form one. The Architecture Planning Input
(`schema_version` 2: constraints in canonical units, scope, and origin) is built from a set,
stored with it and hashed. An architecture built from a set can therefore always name the exact
requirement versions, and through their origin the analysis and the exact words they came from.
The Requirements Engine never produces architecture elements (nodes, services, databases,
queues): that is the Architecture Engine's job, from the planning input.

## Evaluation

`ai/evaluation/datasets/requirements/v1.jsonl` holds 53 labelled descriptions (well specified,
vague, conflicting, incomplete, adversarial, per domain). `make eval` prints precision, recall
and accuracy per stage and every difference from the labels; `make test-eval` fails if any metric
drops below `thresholds.json`, which is only ever raised. At rules-1.0.0: extraction F1 0.963,
classification 0.989, normalization, conflicts, completeness and readiness 1.0, with 3 known
false positives and 4 false negatives (numbers in words, "checkout" read as a payment
requirement, "retain orders" read as an order feature).

## Failure handling and limits

| Situation | Behaviour |
|---|---|
| Empty, over 20,000 characters, control characters, lone surrogates | `422 invalid_requirement_input` before anything runs; bodies over the size limit refused before being read (`413`) |
| Text the rules cannot read | Unresolved → ambiguity; nothing guessed |
| Impossible requirement ("latency between 900 and 100 ms") | Blocking `invalid` finding |
| Model unavailable, timeout, malformed output | Warning; deterministic result stands |
| Adversarial volume | At most 100 candidates and 300 findings per analysis, with a warning for what was left out (blocking findings are never cut); worst case about 0.2 s |
| A very long sentence (a list with no full stop) | Each candidate's statement is the clause around it (at most 400 characters of the user's own words), so it stays promotable |
| Candidate already promoted | Returned with `created: false`; a concurrent duplicate is refused by the database |
| Too many analyses | Rate limit: 60 per user and 120 per IP per hour |

Metrics are structured log events (see [docs/api/requirements.md](api/requirements.md)); their
labels are identifiers only, so requirement text never reaches them, nor the audit log.

## Repository audit

Phase 0 found `engines/requirements/`, `ai/*` and several docs as empty placeholders, and the real
requirement logic already in `core/domain/requirements/` (normalization, validation, conflicts,
completeness, sets, the planning input). The engine was built on that domain rather than beside
it, in the existing folders, with no new top-level packages. Decisions: stored analyses with
explicit promotion; `==` and `between` operators (no `!=`); a provider-agnostic model port with an
Anthropic adapter, off unless configured; a metrics port logged as structured events. See
[ADR-009](adr/ADR-009-requirements-engine.md).

## Definition of done

Each item of spec section 65 is mapped to the tests that prove it in
`tests/security/test_traceability_requirements_engine.py`; the test fails if a mapped test is
renamed or removed.

## Senior review

Spec section 66's questions, answered:

- **Reproducible and deterministic?** Yes. The rules are pure and ordered (no time, randomness or
  set-order dependence); keys are content hashes; the analysis stores the input, its hash, the
  engine version and the result, so a past result is read back, never recomputed.
- **Can a hallucination become canonical?** No. A proposal must quote the input verbatim, pass the
  domain rules, and is only ever a draft `ai` candidate; a person must promote it (as a draft) and
  activate it.
- **Can a user inject instructions?** Their text is delimited data, escaped, outside the fixed
  instructions; the output is schema-constrained and validated item by item. A model that obeys an
  injection still cannot get anything past validation (tested).
- **Cross-organization access?** No. Every call checks project access first (404 for strangers);
  an analysis is fetched by project and id; a composite foreign key keeps a requirement's origin
  in its own project (tenant isolation sweeps cover the new endpoints).
- **History?** Every requirement change is an append-only version; analyses are append-only.
- **Can an old architecture reference its exact requirements?** Yes: through the requirement set
  (pinned versions) and its hashed planning input.
- **Without the model provider?** Yes; the model is optional and its failure is a warning.
- **Is extraction quality improving?** `make eval` measures it; `make test-eval` blocks regressions.
- **Why was a requirement created?** Its origin names the analysis and candidate; the candidate
  has the exact span of the user's text, the method and the confidence; the audit log records the
  promotion and who made it.
- **Fact, user requirement, AI interpretation, assumption, warning, conflict?** The raw input is
  the fact; `source` separates `user`, `system` (rules) and `ai`; findings separate `assumption`,
  `ambiguity`, `conflict`/`consistency` and severities `blocking`/`warning`/`info`.

Known limitations: the rules do not read numbers in words; a few words produce false positives (see
Evaluation); metrics are log lines until an exporter is added; the frontend does not yet use the
analysis API.
