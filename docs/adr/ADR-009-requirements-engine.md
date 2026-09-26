# ADR-009: The Requirements Engine: deterministic first, stored analyses, explicit promotion

- Status: accepted
- Date: 2026-09-26

## Context

People describe systems in prose. ArchitectOS needs structured, validated requirements, and must be
able to say where each one came from. Language models read prose well but hallucinate, follow
instructions embedded in the text, fail, cost money and are not reproducible. Requirements feed
architecture decisions, so an invented or misread requirement is expensive.

## Decision

- **Deterministic first.** A pure rule pipeline in `engines/requirements/` (normalize, extract,
  classify, validate, then ambiguity, assumptions, conflicts, completeness) does all the work it
  can. It never guesses: what it cannot read is reported, and every interpretation it applies is
  surfaced as an assumption.
- **A model only proposes, only when needed.** Behind a provider-agnostic port (`ai/llm`), off
  unless configured (Anthropic adapter). It is asked only for text the rules left unread; the
  user's text is delimited data; the output is a closed schema; each proposal must quote the input
  verbatim and pass the same domain validation. Rules win disagreements. Model failure is a
  warning, never an error.
- **Analyses are stored, append-only.** The raw input exactly as written, its SHA-256, the engine
  version and the full result (`result_schema` 1). A result is read back, never recomputed, so it
  stays reproducible even when the engine or model changes.
- **Candidates are not requirements.** Promotion is an explicit, idempotent act by a person, at most
  100 at a time: candidates are re-read from the stored result (keys re-verified), re-validated,
  and become **draft** requirements carrying their origin (analysis, candidate key). A unique index
  on the live origin prevents duplicates, even under concurrency.
- **Readiness is a severity question.** Findings are `blocking`, `warning` or `info`; an analysis is
  ready for architecture only with no blocking finding (invalid values, contradictions, essential
  areas missing for this kind of system).
- **Bounded.** 20,000 characters per input, 100 candidates and 300 findings per analysis (with a
  notice of what was left out; blocking findings are never cut), rate-limited per user and IP.
- **Measured.** A labelled dataset and a regression gate (`make test-eval`) whose thresholds only
  rise; metrics through a domain port, logged, with identifier-only labels.
- **Boundary.** The engine outputs requirements and findings; the Architecture Engine consumes the
  planning input built from a requirement set. The Requirements Engine never produces architecture
  elements.

## Consequences

- The engine works with no model at all, is deterministic, and is testable as pure functions; it
  runs unchanged in the API, a worker, the evaluation CLI and tests.
- Prose the rules do not understand (numbers in words, unusual phrasing) needs the model or a
  human; the evaluation report lists these gaps, and new rules must not lower any metric.
- Stored results use storage per analysis (at most about 0.25 MB at the caps; a 4 MiB database
  check is the hard limit) and a result schema that must be versioned when it changes.
- Every requirement can be explained: origin → analysis → exact span of the user's words, the
  method, the confidence and the assumptions made.
