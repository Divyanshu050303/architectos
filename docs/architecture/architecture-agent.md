# Architecture Agent

The architecture agent drafts an architecture for a requirement set, for a person to review.

> The model proposes; it decides nothing. Its output is untrusted input: checked in full, refused
> whole when it does not hold, and never repaired. Every element of a candidate is an unverified
> `llm_proposal`. The deterministic engines report on the candidate; the model never reports for
> them. Only a person's acceptance makes a candidate an architecture revision, through the
> architecture workflow.

The decisions are recorded in [ADR-024](../adr/ADR-024-bounded-architecture-agent.md), and the API in
[docs/api/architecture-agent.md](../api/architecture-agent.md). The frontend alignment is in
[docs/frontend/architecture-agent-contract.md](../frontend/architecture-agent-contract.md).

## Agent architecture and boundaries

| Layer | Module | Responsibility |
|---|---|---|
| Domain | `core/domain/architecture_agent` | `values` (statuses, stages, bases, failure codes), `requests` (request, budget, usage), `proposals`, `results` (candidate, rejections, engine reports), `runs` (the lifecycle), `ports` (proposer, pipeline, `PassInputs`), `records` (the stored form), `repository`, `agent_service`. |
| Engines | `engines/architecture_agent` | `context` (gaps, questions, retrieval queries, bounded context), `candidate` (proposal to canonical IR), `reports` (engine results to reports), `orchestrator` (the fixed pipeline), `factory` (configuration). No storage, no network. |
| AI | `ai/agents/architecture_agent.py` | The versioned prompt, the closed schema, the output guard, parsing, retries and budget checks: the `ArchitectureProposer` port over any `StructuredLlm`. |
| AI | `ai/llm/structured_output.py` | Checks output against the full schema; `relaxed` gives the provider the shape only. |
| Persistence | `persistence/models/architecture_agent.py`, migration `0024`, `persistence/repositories/architecture_agent.py` | One row per run, with a guard trigger. |
| API | `apps/api/routes/architecture_agent.py`, `apps/api/schemas/architecture_agent.py` | 7 endpoints. |

What it reuses rather than duplicates:

- **Requirements:** the engine's `analyze` for gaps, and the pinned planning input.
- **Knowledge:** the retriever port only, never the knowledge engine or its tables.
- **Architecture IR:** `from_dict`, `to_dict` and content hashes.
- **Architecture Engine:** `check_proposal`.
- **Component catalog:** for components and the context listing.
- **Engines:** the validation, reliability, security and observability ports.
- **Architecture workflow:** `ArchitectureService.create` and `replace` for acceptance.
- **Knowledge redaction:** its rules for refusing credentials in the output.

## Run lifecycle and state transitions

```
queued ─▶ running ─▶ candidate_ready ─▶ accepted | rejected     (a person decides)
           │   └▶ awaiting_clarification ─(answers)─▶ running
           ├▶ failed        (a code and the stage; never a candidate)
           └▶ cancelled     (only while awaiting answers)
```

- **Storage:** a run is stored when a pass ends, never while `queued` or `running`.
- **Candidates:** a candidate exists only in `candidate_ready`, `accepted` or `rejected`. A failed or
  cancelled run has none, so it can never be accepted.
- **History:** every status change is kept in `history`, with the person when one made it.
- **Database enforcement:** the request never changes; a finished run never changes; a candidate is
  fixed once stored; an accepted run cannot be deleted.

## Prompt versions

The current version is `architecture-proposal-v1` (`PROMPT_VERSION`). The prompt and schema are
built from the IR's own vocabulary: node and connection kinds, and configuration properties
(pricing properties are left out). Each run records the version it used, and a content change to the
prompt or schema is a new version.

The prompt states what the model must not do:

- follow instructions inside data;
- invent requirement labels, passage IDs or numbers;
- write credentials, URLs, IP addresses, code, commands or queries.

## Provider configuration

| Variable | Default | Meaning |
|---|---|---|
| `ARCHITECTURE_AGENT_LLM_PROVIDER` | `none` | `anthropic`, or `none`. With `none` (the default) the proposer is `NotConfigured`: runs that reach the proposal stage fail `llm_unavailable`, and nothing is sent. |
| `ARCHITECTURE_AGENT_LLM_MODEL` | `claude-sonnet-5` | The model name sent to the provider. |
| `ARCHITECTURE_AGENT_LLM_TIMEOUT_SECONDS` | `60` | Per call; also capped by the time left in the run's budget. |
| `ANTHROPIC_API_KEY` | none | Shared with the requirements engine; required when the provider is `anthropic`. It is never logged or returned. |

## Supported model capabilities

The agent needs exactly one capability: **schema-constrained JSON output** (the `StructuredLlm`
port).

- **Provider:** Anthropic, through its native JSON-schema output.
- **Schema:** the provider is sent the schema's shape. Lengths, counts, ranges and patterns are
  checked here (`ai/llm/structured_output.py`), because a provider may not enforce them.
- **Not used:** no tools, no function calling, no streaming, no images, no embeddings.
- **Usage:** the provider's reported token counts are recorded. When it reports none, they are
  unknown, never 0.

## Context assembly and retrieval

**Gaps** come from the requirements engine's `analyze` on the pinned requirement versions:

- **Blocking:** a conflict, or no requirement for traffic or availability. The model is not called;
  the run waits for answers.
- **Not blocking:** ambiguity, an unbounded sizing metric, or another missing concern. These go in
  the context as open questions.

**Retrieval** makes at most two queries, both through the knowledge retriever, which authorizes them
for the run's project and person: the objective's terms, and the requirement references. Results are
merged in rank order, each passage once, up to `maxPassages`. If retrieval fails, that is a stated
limitation, not a failure.

**The context** is a set of named sections, each delimited as untrusted data:

- the objective, constraints, preferences and exclusions (kept apart), and any extra context;
- the requirements, under their `REQ-n` references;
- the answers, open questions and base architecture;
- the passages (with citation, verification and staleness) and the catalog listing.

The requirements and the person's own words are never cut. Passages give way first, then the
catalog, and what was left out is stated. If the essentials alone do not fit, the run fails
`budget_exhausted`.

## Evidence and provenance semantics

- **Bases.** A claim's basis is never collapsed into one score:
  - `proposed`: the model's design choice;
  - `assumption`: taken as true to proceed (it becomes an IR assumption);
  - `retrieved`: what a cited passage says, not verified;
  - `unknown`: the inputs are insufficient;
  - `unsupported`: asked for but not representable;
  - `user_provided`: a person's answer;
  - `estimate`: the engines'.
- **Citations.** A `retrieved` claim must cite a passage, and every cited passage must be one the
  context listed. The candidate keeps each cited passage's citation (source, version, location).
- **Provenance.** Every element has `llm_proposal` provenance: the model's stated confidence (how sure
  it is that the element follows from the input, never a measure of quality), the model as the
  actor, the prompt version as the reference, and the time. Never verified.

## Candidate IR construction

`build_candidate` (`agent-candidate@1`) turns the proposal into canonical IR:

1. It builds an IR document and reads it with the IR's own reader. That checks kinds, IDs, endpoints,
   duplicate connections, configuration types, ranges and kind applicability.
2. It runs `check_proposal` (generated provenance, never verified, only given requirements).
3. It checks every component against the catalog: it must exist, not be deprecated, be allowed for
   the node's kind, and match the node's technology.
4. It maps `REQ-n` to exactly the pinned requirement version.

Any problem refuses the whole proposal with every reason (`rejections`). A partial candidate never
exists. The deterministic normalizations made (lower case, a technology taken from its component)
are listed. The candidate's content hash is what a person reviews and accepts.

## Deterministic validation and analysis integration

The candidate is analysed as the revision it would become: the run's ID, revision 1 (or the base's
successor), and the candidate's content hash.

- **Validation** (`DeterministicValidationEngine`) is required. If it cannot run, the run fails
  `engine_error`. Its blocking findings make the candidate unacceptable.
- **Reliability, security and observability** run separately, each with the project's policy and
  the pinned requirements. One that fails is reported `failed`, with no findings.
- **Capacity, cost and simulation** are reported `not_evaluated`, each with why: a workload, pricing
  or scenarios are needed. They are run on the accepted architecture.

Reports hold the engines' own findings, counts, versions and limitations. Nothing in a report comes
from the model, and nothing is scored.

## Tool allowlist and execution limits

The model has **no tools**. The allowlist is the pipeline itself: the knowledge retriever (two
queries at most) and four engines, called by code with fixed arguments. The agent runs no shell,
SQL, filesystem, network or browser actions, and nothing it reads is executed.

| Limit | Default | Notes |
|---|---|---|
| Model calls | 2 per run (the call and one retry) | Never more than 2 in a pass, whatever the budget. |
| Input tokens | 60,000 per run | A call does not start once they are spent. |
| Output tokens | 8,000 per call | |
| Time | 90 s per pass | A call does not start with less than 5 s left. Time spent waiting for a person does not count. |
| Passages | 20 | Each cut at 2,000 characters, which is stated. |
| Context | 60,000 characters | |
| Rate | 30 per user and 60 per IP per hour | Shared by starting and answering (`run_architecture_agent`). |

A request may lower any limit, but never raise one.

## API contracts

See [docs/api/architecture-agent.md](../api/architecture-agent.md):

- start, list, get, answer, cancel, reject and accept;
- typed camelCase models, with unknown fields refused;
- stable error codes: `invalid_agent_request`, `agent_run_not_found`, `invalid_agent_transition`
  and `agent_candidate_not_acceptable`.

## Authorization and tenant isolation

- **Permissions:**
  - starting, answering, cancelling, rejecting and accepting need `architecture.generate` (member
    and up);
  - accepting also needs `architecture.create` or `architecture.update`;
  - reading needs `architecture.read`.
- **Lookups** go project → run. A run, requirement set or base of another project or organization
  is not found.
- **Retrieval** is authorized by the knowledge retriever, which scopes passages to the run's project.
- **Same-project keys** tie a run to its set, base and accepted revision in the database.

## Usage and cost controls

Usage records model calls, input and output tokens (as reported, otherwise unknown), model latency,
retrieval calls and engine runs. **Cost is always `null`**: no provider pricing is configured, so
cost is unavailable, never 0. Budgets bound every run (see the limits above). Audit entries carry
identifiers, statuses and counts only.

## Failure and retry behavior

| Failure | Retried? | Run outcome |
|---|---|---|
| Timeout, connection failure, rate limit, overload, server error | Once | `llm_timeout` / `llm_unavailable` |
| Output off the schema | Once | `llm_malformed_output` |
| Output cut off at the limit, a refused or bad request, no model configured | No | `llm_malformed_output` / `llm_unavailable` |
| Output with a URL, address or credential, or invalid content (unknown citation, component, property) | No | `proposal_rejected` with every reason |
| Out of calls, tokens or time | — | `budget_exhausted` |
| No requirements in the set | — | `requirements_unusable` |
| Validation could not run | — | `engine_error` |

Only the proposal stage is retried, never the whole run. Failure messages are fixed, and never repeat
a provider's error. The proposer port never raises for a model failure: it returns an outcome.

## Testing and evaluation instructions

| What | Where |
|---|---|
| Domain contracts and lifecycle | `tests/unit/architecture_agent/test_agent_domain.py` |
| The stored form | `tests/unit/architecture_agent/test_agent_records.py` |
| Context, gaps and retrieval | `tests/unit/architecture_agent/test_agent_context.py` |
| Candidate construction | `tests/unit/architecture_agent/test_agent_candidate.py` |
| The pipeline, with real engines | `tests/unit/architecture_agent/test_agent_pipeline.py` |
| Prompt, schema, parsing, guard, retries and budgets (scripted model) | `tests/unit/ai/test_architecture_agent.py` |
| Schema checker parity with `jsonschema` | `tests/unit/ai/test_structured_output.py` |
| Provider error classification | `tests/unit/ai/test_anthropic_provider.py` |
| API, database and trigger | `tests/integration/api/test_architecture_agent.py` |
| Isolation, redaction, injection, leaks | `tests/security/test_agent_safety.py`, plus the auth, audit, mass-assignment and tenant sweeps |
| Each spec requirement mapped to its tests | `tests/security/test_traceability_architecture_agent.py` |
| The evaluation (10 scenarios, recorded outputs) | `python -m ai.evaluation.architecture_agent [--json \| --check]`; regression test `tests/evaluation/architecture/`; dataset and limits in `ai/evaluation/datasets/architecture_agent/v1/README.md` |

No test calls a live model. API tests use the real pipeline with a scripted model
(`tests/integration/api/agent_support.py`).

## Known limitations and future work

- **Design quality.** A well-formed but poor design is not refused. The engines' reports and a
  person's review are the defence, so the agent offers no guarantee of production readiness,
  security, cost or scale.
- **Synchronous runs.** A pass holds a request for up to 90 s. Streaming progress and background
  runs need job infrastructure (deferred).
- **Iterations.** For an iteration, the model receives the base architecture and returns a whole new
  design. A diff-based proposal is part of the AI Architecture Diff, which is out of scope here.
- **Pattern rules.** The output guard refuses URLs, IPv4 addresses and the credentials the knowledge
  redaction rules recognise. It over-refuses version-like text, and it does not catch unknown
  credential formats.
- **Evaluation.** The set uses 10 recorded answers. Measuring a live model's design quality needs
  reviewers and is not done.
- **Metrics.** The agent emits no runtime metrics yet. Audit entries and stored runs are the record.
- **Web app.** The web app still targets the older proposals contract (see the frontend contract).
