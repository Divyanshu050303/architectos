# AI Architecture Diff

> AI Architecture Diff explains architectural changes; it does not determine architectural
> correctness by itself.

It compares two exact states of a project's architecture. It says what changed, what that touches,
and what the deterministic engines find in each state. A language model may then explain the stored
result, on request, in statements that cite what they rest on. The diff changes nothing: no
architecture, revision, agent run or analysis is written, approved, migrated or deployed.

The decisions are recorded in [ADR-025](../adr/ADR-025-grounded-architecture-diff.md). The API is in
[api/architecture-diffs.md](../api/architecture-diffs.md), and the web app's contract in
[frontend/architecture-diff-contract.md](../frontend/architecture-diff-contract.md).

| Part | Where |
|---|---|
| Domain: requests, changes, groups, impacts, explanations, records, service | `core/domain/architecture_diff/` |
| Semantic diff (`diff-semantic@1`) | `engines/architecture_diff/semantic.py` |
| Requirement and ADR impact (`diff-traceability@1`) | `engines/architecture_diff/traceability.py` |
| Engine comparison (`diff-impact@1`) | `engines/architecture_diff/impact.py` |
| The comparison, composed | `engines/architecture_diff/engine.py` |
| Explanation context (`diff-explanation-context@1`) and interpretation | `engines/architecture_diff/explanation_context.py`, `interpreter.py` |
| The explanation agent (`diff-explanation-v1`) | `ai/agents/diff_agent.py` |
| Shared model guard (data sections, output guard, output hash) | `ai/llm/guard.py` |
| Storage (migration 0025) | `persistence/models/architecture_diff.py`, `persistence/repositories/architecture_diffs.py` |

## Architecture diff semantics

A comparison takes:

- two states, `base` and `target`, which must differ;
- optionally, the requirements to report on;
- optionally, a stored capacity analysis and a stored cost analysis, whose inputs are reused;
- optionally, the person's own words about the change;
- whether to explain now.

A state is one of:

- **a revision:** an architecture of the project and a revision number;
- **a candidate:** an architecture agent run of the project with a candidate (accepted, rejected or
  waiting).

A state is never "the latest", never a name, and never an observed system. Observed states are drift
detection's: compare them there.

The result, an `ArchitectureDiff`, records:

- both states, by reference, content hash and label (never copies of either architecture);
- the semantic diff;
- the requirement and ADR impacts;
- the engines' comparison;
- warnings and unknowns.

It is stored once and never changes. Explanations are separate, appended runs.

## Stable identity rules

Elements are matched by their **stable id**, never by name or position:

- **Same id, different name.** The same element, modified (`renamed: true`).
- **Different id, same name.** A removal and an addition. It is never guessed to be the same thing.
- **Change ids.** A change's id is `ch_` and a digest of its element type and element id. The same
  pair of states always gives the same change ids.
- **Group ids.** A group's id is `cg_` and a digest of its rule and its change ids.
- **The architecture's own fields** (name, description, its own requirement references) are one
  change with element id `architecture`.
- **Different architectures.** Comparing states of different architectures matches by id alone, and
  a warning says so.

## Deterministic and AI layers

| Layer | What | How |
|---|---|---|
| A. Deterministic diff | Changes, field deltas, classes, groups | The IR's diff, classified and grouped by rules. Same input, same output |
| B. Deterministic impact | Requirement relations, ADRs to review, engine findings and measures | Traces, validation verdicts and the engines on both states. Same input, same output |
| C. AI interpretation | A summary, group explanations, trade-offs, requirement explanations, risks, review questions, unknowns | One model call over A and B (at most one retry). Optional, appended, never changing A or B |

Layers A and B never depend on layer C. An explanation may vary between calls; the diff never does.

## Change classification

A change carries field deltas, each with:

- its `path`;
- both values in canonical form;
- the value types (`absent`, `text`, `number`, `boolean`, `list`, `object`, `redacted`);
- a unit, when the property's name states one (`_per_second`, `_bytes`, `_seconds`, `_cores`,
  `_ratio`);
- its classes;
- its sensitivity.

Classes say what a change **concerns**, never what it does:

- **From the IR's category of the field:**
  - `kind` is `structural`;
  - `technology` is `technology`;
  - endpoints, semantics and placement are `topology`;
  - traceability is `requirement`;
  - lifecycle is `operational`;
  - metadata, provenance and description are `metadata`.
- **From which engines read the property** (migration planning's tables):
  - capacity gives `performance`;
  - cost gives `cost`;
  - reliability gives `reliability`;
  - security gives `security`;
  - observability gives `observability`;
  - scaling properties give `scaling`.
- **Anything else** is a `configuration` change, or `unknown` for a setting the IR doesn't define.

**Secrets.** A key that looks like a credential, by the IR's own `is_secret_path`, is `secret`. Both
values are `redacted`; the change is reported and its values never are.

**Groups.** Every change is in exactly one group, formed by these rules in order:

1. `connected_changes`: changed nodes, joined through changed connections' endpoints. Never through
   an unchanged connection.
2. `traceability`: only requirement references changed.
3. `descriptive`: only names, descriptions, metadata or provenance changed.
4. `recorded_reasoning`: assumptions and decision references.
5. `architecture`: the architecture's own fields.

A group's `reason` says, in the rule's words, why its changes are together.

## Impact analysis

**Requirements** (`diff-traceability@1`) are related only through the architecture's own traces and
the validation engine's verdicts, never by their wording:

- `directly_changed`: a trace was added, removed or moved to another version on a changed element.
- `element_changed`: an element traced to it changed.
- `potential`: the validation verdicts differ between the states. Never "violated".
- `no_relationship` and `undetermined`: only for requirements the person asked about.
- **Unreadable references.** A requirement referenced but unreadable is listed in `unknowns` by id,
  never invented.

**ADRs.** A proposed or accepted ADR is listed when an element it concerns changed. Its elements come
from the ADR's own related elements, and from the IR's decision references in either state. Its note
is "May require review". Nothing more is claimed.

**Engines** (`diff-impact@1`) run on both states with the same requirements and policy:

- **Validation, reliability, security and observability.** Findings are compared by stable id.
  Introduced and resolved findings are listed; unchanged ones are counted. Each state's own summary
  is kept.
- **Capacity.** Runs only on the workload (and entry points) of a stored capacity analysis of a
  compared architecture. Bottlenecks are compared by node and resource. `highest_utilization` and
  `saturation_multiple` are reported only when both states have them.
- **Cost.** Runs only on the pricing snapshot, date, hours and provider of a stored cost analysis.
  Both states are priced at their declared resources. `known_monthly_total` is reported only when
  both states could be priced, with a lower-bound limitation when partial.
- **Simulation** is not run. A scenario belongs to one architecture, so run simulations on each
  state.
- **Identical states** are not analyzed. **An engine that fails** is reported `failed`
  (`engine_error`), and the others still compare.

No score, rank, rating or winner is produced anywhere. `counts` are counts.

## Evidence and grounding

**The context** (`diff-explanation-context@1`) is the diff's own facts, as named data sections:

- states and the person's context;
- changes (with field details, secrets shown only as "a secret value changed");
- groups;
- requirements and decisions;
- engines (findings by `engine:finding id`, measures, limitations);
- unknowns;
- passages.

Nothing else of the project is included.

**Citable references.** Each section lists what a statement may cite, by basis:

| Basis | Reference |
|---|---|
| `change` | `ch_…` |
| `group` | `cg_…` |
| `finding` | `engine:finding id`, evaluated engines only |
| `requirement` | `REQ-n` |
| `decision` | `ADR-n` |
| `evidence` | a passage's chunk id |
| `user_input` | `context`, only when the person gave one |

**Retrieval.** Knowledge is retrieved through the knowledge retriever, which authorizes the caller
for the project:

- at most two queries: the changed elements' names and technologies, then the requirement and
  decision references;
- at most 10 passages, each cut to 2,000 characters.

A retrieval failure is said in `limitations`, and the explanation proceeds without passages. An
explanation run keeps the citations of the passages it cites, never their text.

## AI explanation lifecycle

1. **Identical states** are `not_needed`: no retrieval, no model call.
2. **Retrieval.**
3. **Context.** Assembled within the budget. Field details give way first, then passages, and what
   was left out is said. If even the changes' headers don't fit, the run fails `budget_exhausted`
   and nothing is sent.
4. **One model call,** with a versioned prompt (`diff-explanation-v1`), the closed schema (the
   provider gets only its shape), and the data as delimited, untrusted sections. A forged section
   tag in the data is escaped.
5. **Checks,** all of which must pass, or the output is refused whole and never repaired:
   1. the full schema;
   2. the output guard (no URL, IP address or credential);
   3. the domain's constructors (every statement grounded or `inferred`);
   4. citations only of what the context listed, and groups and requirements only from the diff;
   5. questions that ask;
   6. no score language;
   7. no number the data doesn't state (counts up to 10 excepted);
   8. no outcome stated as fact (faster, cheaper, more secure, improves…) unless it cites an engine
      finding.
6. **Retry.** Only a timeout, a temporary provider failure or output off the schema is retried, once.
   A refused output is not asked for again.
7. **Stored as an appended run:**
   - `completed`, `failed` (`llm_unavailable`, `llm_timeout`, `llm_malformed_output`,
     `explanation_rejected` or `budget_exhausted`), or `not_needed`;
   - the model, prompt version and usage;
   - the SHA-256 and size of the output;
   - the rejections (where, never what);
   - the cited evidence;
   - the limitations.

**Configuration.** It is off by default:

| Variable | Default | Meaning |
|---|---|---|
| `ARCHITECTURE_DIFF_LLM_PROVIDER` | `none` | `anthropic` to explain with a model (needs `ANTHROPIC_API_KEY`) |
| `ARCHITECTURE_DIFF_LLM_MODEL` | `claude-sonnet-5` | The model |
| `ARCHITECTURE_DIFF_LLM_TIMEOUT_SECONDS` | `45` | Per call, at most 60 |

Without a provider, every explanation fails `llm_unavailable` and nothing is sent anywhere.

## API contracts

| Endpoint | Permission | Purpose |
|---|---|---|
| `POST /projects/{projectId}/architecture-diffs` | `architecture.analyze` | Compare two states, store the diff; with `explain`, append one explanation run |
| `GET /projects/{projectId}/architecture-diffs` | `architecture.read` | List diffs, newest first, optionally by architecture |
| `GET /projects/{projectId}/architecture-diffs/{diffId}` | `architecture.read` | A diff with every explanation run |
| `POST /projects/{projectId}/architecture-diffs/{diffId}/explanations` | `architecture.analyze` | Append an explanation run |

The shapes, errors and rate limits are in [api/architecture-diffs.md](../api/architecture-diffs.md).
There is no endpoint to update, delete, approve, apply or deploy anything.

## Security

- **Untrusted inputs.** Architecture content, ADRs, requirements, documents, passages and the
  person's context are all untrusted data. They are delimited and never instructions.
- **What the model gets.** It receives no tools. It can't choose a step, reach another project,
  change a revision or approve anything.
- **Secrets.**
  - IR secret values are redacted in the diff, so they're never stored, returned or sent.
  - Document secrets are redacted at ingestion by the knowledge engine.
  - The output guard refuses credentials in an answer.
- **What is stored.** Prompts, context, retrieved text and raw output are never stored or returned;
  only the output's hash and size are. Model errors are recorded by code.
- **Audit.** Entries (`architecture_diff.created`, `architecture_diff.explained`) carry identifiers,
  statuses and counts, never content.
- **Append-only.** A database trigger refuses updates, deletes and truncation on
  `architecture_diffs` and `architecture_diff_explanations`.

## Tenant isolation

- **Scoped lookups.** Every lookup goes project → diff, and every state is resolved within the
  project.
- **Same-project foreign keys** link a diff to its revisions or agent runs, and an explanation run
  to its diff.
- **One answer for every unavailable state.** A state that is missing, in another project or
  organization, or without a candidate, gets `404 compared_state_not_found` with `details.side`.
- **Hidden diffs.** A diff of another project or organization is `architecture_diff_not_found`. A
  stranger gets `project_not_found` before anything else is looked up.
- **Knowledge.** It is retrieved with the caller's identity, for the diff's project only.

## Performance limits

| Limit | Value |
|---|---|
| Changes in a diff | 2,000 (more is `architecture_diff_too_large`, never cut) |
| Field deltas per change | 200, and a value over 2,000 characters; more is refused (`architecture_diff_too_large`, `details.limit` `fields` or `value`) |
| Groups | 500 (more is refused, `details.limit` `groups`) |
| Requirements read | 5,000 in force (beyond, a warning) and 200 scoped |
| ADRs read | 500 |
| Explanation context | 60,000 characters |
| Passages | 10, each up to 2,000 characters |
| Model calls | 2 per explanation |
| Model input tokens | 60,000 |
| Model output tokens | 6,000 |
| Model time | 60 s |
| Rate limits | `compare_architectures` 120 per user per hour; `explain_architecture_diff` 30 per user and 60 per address per hour |
| Stored parts | 16 MiB per diff; 1 MiB per explanation |

Comparisons are synchronous, with no cache and no job queue. The engines run on a worker thread,
outside any transaction.

## Evaluation methodology

`python -m ai.evaluation.architecture_diff` runs 11 architecture pairs
(`ai/evaluation/datasets/architecture_diff/v1/`):

- scaling;
- database migration with an ADR;
- adding a cache;
- security exposure;
- no change;
- requirement trace;
- secret rotation;
- prompt injection;
- hallucinated citation;
- unsupported claim;
- model timeout.

Each pair goes through the real comparison, the stored record and the real interpretation, with
recorded model outputs. The metrics are:

- change, classification, grouping, impact, explanation and rejection accuracy;
- grounding integrity, injection containment and record integrity;
- ceilings for secret leaks, unsupported explanations, score language, calls over budget and stored
  leaks.

Every threshold is the measured value, and `tests/evaluation/architecture/test_diff_regression.py`
fails on any regression. The set measures what the diff does with an answer, not how good a live
model's explanations are. Run the tests with:

```text
make test-unit test-integration test-security test-eval migrate-check
```

## Known limitations

- **Pattern rules.** These checks miss some forms:
  - numbers written in words;
  - score phrasings outside the list ("winner", "rated", "n/10", "n% better");
  - outcome wordings outside the list.

  The prompt asks against all three, but no guarantee is claimed.
- **Correctness is not judged.** A well-formed, grounded, unhelpful explanation is accepted. The diff
  does not judge correctness. That is for the engines and the reviewer.
- **Identity across architectures.** Elements are matched by id only. A component re-identified
  across architectures is reported as removed and added.
- **Capacity and cost.** They need a stored analysis of a compared architecture, and cost is priced
  at declared resources. Simulation is not run.
- **Agent candidates.** A candidate is analyzed as revision 1 of its run. Validation findings tied to
  revision numbers are compared by finding id only.
- **No cache, synchronous.** A large diff with an explanation blocks its request for the model's
  time.
- **Future work** (Autonomous Architecture Workflow, #20):
  - compare in a workflow step;
  - link a diff to a migration plan;
  - acknowledge an explanation;
  - background explanations.
