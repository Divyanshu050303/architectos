# ADR-024: A bounded architecture agent: fixed pipeline, untrusted output, human acceptance

- Status: accepted
- Date: 2026-10-03

## Context

ArchitectOS turns requirements into architectures through deterministic engines. A language model
can draft a design from requirements and project knowledge faster than a person starts from a blank
canvas. But a model:

- invents components, numbers and citations;
- follows instructions planted in the text it is given;
- answers differently each time;
- can loop or spend without limit if left to choose its own steps.

None of that may reach an architecture revision, a review, or a person's decision unmarked.

What the repository audit found:

- A provider-agnostic `StructuredLlm` port with one provider (Anthropic, JSON-schema output, no
  tools, no retries).
- The requirement extraction agent's pattern: a versioned prompt, delimited untrusted data, a closed
  schema, rejection rather than repair.
- The Architecture Engine contract (`check_proposal`: generated provenance, never verified, only
  given requirements). No generator existed.
- `ArchitectureService.create` and `replace` with a stale-base check and a `then` hook. Discovery
  already accepts proposals through them.
- The knowledge retriever port (cited, project-scoped passages).
- Deterministic engines callable on an in-memory IR.
- No background-job infrastructure.

## Decision

### A fixed pipeline, not an agent loop

A run passes through the same stages in the same order: interpretation, retrieval, context,
proposal, construction, validation, analysis, review. The model is called at one stage, at most
twice: the call and one retry of a failure that can recover (a timeout, a temporary provider failure,
or output off the schema). It chooses no step and calls no tool. The provider is sent no tools.
Every other stage is deterministic code. Each pass is bounded in calls, tokens, time, passages and
context size, and a pass that runs out stops with `budget_exhausted`.

### The model's output is untrusted input

- **Schema.** A closed schema is checked in full here; the provider is sent only the shape.
- **Content.** Text with a URL, an IP address or a credential is refused whole.
- **References.** Cited requirements and passages must be ones the context listed.
- **Construction.** The proposal becomes canonical IR through the IR's own reader and
  `check_proposal`, all or nothing, never repaired.
- **Provenance.** Every element is `llm_proposal` with the model's stated confidence, never verified.
- **Claims.** Every claim keeps its basis: `proposed`, `assumption`, `retrieved`, `unknown` or
  `unsupported`. `user_provided` comes only from people, and `estimate` only from engines.

### Deterministic engines report; the model never does

- **Validation** is required: without it there is no candidate.
- **Reliability, security and observability** are reported each on their own; one that fails is
  reported as failed.
- **Capacity, cost and simulation** need inputs a candidate does not have (a workload, pricing,
  scenarios). They are reported as `not_evaluated`, with why. No number is invented, and cost is
  unavailable rather than zero.

### Gaps are asked, not assumed

The requirements engine's analysis of the pinned set decides what blocks the run:

- **Blocking:** a conflict, or no traffic or availability requirement. The run waits for a person,
  and the same run resumes with the answers as the person's statements.
- **Not blocking:** other gaps are listed as open questions.

### A person accepts

A candidate becomes an architecture only through `ArchitectureService.create` or `replace` (source
`ai`):

- naming the reviewed candidate by its content hash;
- on a base that is still current;
- with no blocking validation finding.

The run records the acceptance in the revision's own transaction.

### Synchronous runs, minimal retention

There is no new job system: a pass runs within the request, bounded. Each run stores:

- the prompt version, the model and the usage;
- the validated proposal, the candidate and the reports;
- the SHA-256 and size of the raw output.

It never stores the prompt, the context, retrieved text or the raw output. A trigger keeps the
request immutable, finished runs frozen, the candidate fixed and accepted runs undeletable.

## Consequences

- **Reviewable designs.** Reviewers see a design whose every element is marked as unverified, traced
  to requirement versions and cited passages, and checked by the engines before they decide.
- **Fewer accepted answers.** A well-formed but poor design is not refused by the agent. The engines'
  findings and the reviewer are the defence, so the agent offers no guarantee of correctness.
- **Request time.** Runs block a request for up to the time budget (90 s). Streaming progress and
  background runs would need job infrastructure and are deferred. The frontend shows one honest
  step.
- **Lost model text.** No raw output means a provider's explanation of a malformed answer is gone.
  Only that an answer existed, its hash and its size remain. This is deliberate.
- **Agent sends nothing by default.** Without a configured model, a run fails `llm_unavailable` and
  nothing is sent anywhere. The agent is off by default.
- **Evaluation scope.** The evaluation set uses recorded outputs. It measures the guardrails, not a
  live model's design quality.
