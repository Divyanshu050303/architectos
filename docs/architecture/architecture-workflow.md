# Architecture workflow

> The architecture workflow is a bounded autonomous architecture engineering workflow. It proposes and
> analyzes candidate architectures; it does not determine architectural correctness, and nothing it
> produces becomes an architecture without a person's approval.

A person gives a goal. The workflow carries it, step by step, through requirements, project knowledge,
generation, validation, analysis, improvement and comparison to a **review package**. A person then
approves one candidate, or rejects the package. Every step is chosen by deterministic server code
from a closed registry of actions, runs with the starting person's current permissions, and is
recorded. A language model is called only inside two existing components: the requirements engine
and the architecture agent. It never chooses what happens next.

Code: `core/domain/architecture_workflow/` (state, planner, controller, registry, service),
`engines/architecture_workflow/` (executors over the engines), `persistence/` (records, queue),
`workers/workflow_worker.py`, `apps/api/routes/architecture_workflows.py`. API:
`docs/api/architecture-workflows.md`. Decision: ADR-026.

## Autonomous workflow architecture

```
person ── POST /architecture-workflows ──▶ ArchitectureWorkflowService ──▶ architecture_workflows (queued)
                                                                              │
workers/workflow_worker.py ── claim (FOR UPDATE SKIP LOCKED, lease) ◀────────┘
        │
        ▼
WorkflowController.advance ── snapshot ─▶ plan (deterministic) ─▶ authorize (registry, stage, permission)
        ▲                                                             │
        │                                                             ▼
        └──── commit (workflow + candidates + step, one transaction) ◀── executor
                                                                       │
                       requirements engine · knowledge retriever · architecture agent pipeline ·
                       validation · reliability · security · observability · capacity · cost ·
                       simulation · evolution rules · architecture diff
```

It reuses the earlier milestones rather than duplicating them:

| Need | Reused from |
|---|---|
| Requirement extraction | Requirements Engine (`RequirementAnalysisService`, stored analyses) |
| Pinned requirements | Requirement sets |
| Knowledge | `KnowledgeService.retrieve` (authorized, lexical, cited) |
| Generation and agent revisions | Architecture agent pipeline (structured output, guard, catalog, IR builder) |
| Validation and analyses | The deterministic engines |
| Improvements | Evolution rules (`by_rule`) |
| Comparison | Architecture diff engine |
| Approval | `ArchitectureService.create` / `replace` (versioning, optimistic concurrency) |
| Permissions, audit, tenancy | `project_access`, audit log, project-scoped repositories |

No workflow engine, queue system or new infrastructure was added. The queue is the
`architecture_workflows` table.

## State machine

```
queued ─▶ running ─▶ review_ready ─▶ approved | rejected     (a person decides)
  ▲         │  │          └──────▶ cancelled
  │         │  └▶ needs_input ─(a person's input)─▶ queued
  │         ├▶ failed       (code, class, stage)
  │         └▶ cancelled    (also from queued and needs_input)
  └─────────┘ (released: the worker's turn ran out, or its lease expired)
```

Any other move is refused (`invalid_workflow_transition`). `approved` is reachable only from
`review_ready`, by a person. Every status change is kept in `history`, with who and when.

The record holds itself consistent:

- `needs_input` always carries an input request;
- `failed` always carries a failure;
- `rejected` always carries a reason;
- `approved` names a selected candidate and the revision it became;
- finished workflows have `completed_at`.

The same rules are database check constraints. A guard trigger freezes a finished workflow and the
request columns.

## Stage lifecycle

| Stage | Actions |
|---|---|
| `intake` | (created) |
| `requirements` | `analyze_requirements`, then `request_clarification` (confirm requirements), without a pinned set |
| `knowledge_retrieval` | `retrieve_knowledge`, once (skipped and said when search fails) |
| `generation` | `generate_architecture`: the agent's candidate, or `request_clarification` for its blocking questions |
| `validation` | `validate_architecture` per candidate |
| `analysis` | reliability, security, observability per validated candidate; capacity, cost and simulation only with their stated inputs |
| `iteration` | `generate_alternative`: an improvement of the newest candidate |
| `comparison` | `compare_candidates`: the architecture diff against its parent, or the base revision |
| `review` | `prepare_review`: the validated candidates, newest first, at most 8 |
| `decision` | a person's approval or rejection |

## Planner and controller responsibilities

**Planner** (`planner.py`): a pure function of the workflow, its candidates, its steps and which
inputs it has. It returns the one next action, or a stop with a reason. The order:

1. requirements;
2. knowledge;
3. generation;
4. for each candidate: validate, the applicable analyses, compare;
5. iteration;
6. review.

Before an action that costs (a model call, a candidate, an iteration, a retrieval, a simulation), the
budget is checked. When a limit is reached, the workflow goes to review if any candidate is valid
(the limit is recorded as a limitation). Otherwise it stops: `budget_exhausted`, `timed_out` or
`no_valid_candidate`.

**Controller** (`controller.py`): each turn it does the following.

1. **Read** a snapshot.
2. **Plan.**
3. **Authorize:** the action is in the registry, automatic, allowed in this stage while running, and
   the person still holds its permission.
4. **Execute** through the action's executor. Failures are outcomes, never exceptions.
5. **Commit** the step and its effects in one transaction, only if the stored workflow is still the
   one it read.

A result that does not hold stops the workflow `engine_error`, with nothing of it kept. An example
is an attempt to rewrite an existing candidate's architecture.

## LLM responsibilities

| The model may | The model may not |
|---|---|
| Extract requirement candidates from the goal (requirements engine) | Choose, name or add an action |
| Propose an architecture as structured output (agent) | Approve, reject, cancel or change a workflow's state |
| Ask clarification questions (agent; blocking ones pause the workflow) | Read another project's data |
| Revise a candidate for a named finding when no rule applies (agent) | Skip validation or any engine |
| | Become an architecture without a person |

Its output is validated against a schema and checked by the output guard. That refuses URLs,
credentials, unknown components and citations of passages that were not retrieved. A refused answer
fails the step (`proposal_rejected`) and is never used. Deterministic engines decide every finding;
the model's statements stay unverified `llm_proposal` provenance and stated rationale.

## Tool registry

`tools.py` holds 14 actions, each with a description, a side-effect class, a permission, the stages it
may run in, whether a failure may be retried, and whether it may call a model:

`analyze_requirements`, `request_clarification`, `retrieve_knowledge`, `generate_architecture`,
`validate_architecture`, `run_capacity_analysis`, `run_cost_analysis`, `run_reliability_analysis`,
`run_security_analysis`, `run_observability_analysis`, `run_simulation`, `generate_alternative`,
`compare_candidates`, `prepare_review`.

There is no generic code, shell, SQL, browsing or HTTP tool. An unknown action is refused
(`unknown_action`), as is a known one outside its stage (`wrong_stage`) or while not running
(`not_running`). `build_executors` builds exactly one executor per registered action.

## Side-effect policy

| Class | Automatic | In the registry |
|---|---|---|
| `read_only` | yes | `retrieve_knowledge` |
| `analysis` | yes | validation and the six analyses |
| `candidate_mutation` | yes | requirement analysis, clarification, generation, alternatives, comparison, review (workflow records only) |
| `canonical_mutation` | **never** | none |
| `external_side_effect` | **never** | none |

Confirming requirements and approving a candidate are a person's own requests, under their own
permissions, through the service. A source scan test checks that no module the workflow runs on its
own writes an architecture, imports a network or process module, or evaluates code.

## Approval model

Only `ArchitectureWorkflowService.approve`, called by a person, writes an architecture. It requires
`architecture.generate`, plus `architecture.create` for a new architecture or `architecture.update`
for a revision of the base. The candidate must:

- be in the review package;
- have exactly the reviewed content hash;
- have been evaluated by validation with no blocking finding (not evaluated is not "no findings").

With a base, it is a revision of exactly that revision. When the base is no longer current, approval
is refused (`stale_candidate`, inside the revision's own transaction). Approval of a workflow, its
candidate's `accepted` status and the revision commit together or not at all. Rejection records a
reason.

## Candidate lifecycle

```
generated ─▶ validated ─▶ selected_for_review ─▶ accepted
    │            └──────▶ superseded ◀────────┘
    └▶ rejected (validation blocks it; kept)
```

- **Lineage.** The first candidate is the agent's. Every improvement names its parent and the
  `trigger`: the parent's finding it answers (engine, rule, severity, elements). Its origin is
  `rule` (with the evolution rule's id) or `agent_revision`.
- **Rules first, model second.** An actionable finding (critical or high) is answered by a
  deterministic evolution rule when one applies. Otherwise the agent revises the candidate for it,
  and a validation block always goes to the agent. A design identical to an earlier one is not kept
  (`no_new_design`).
- **Immutable.** A candidate's IR is checked against its content hash on every read. A guard trigger
  lets only its status and reports change.

## Checkpointing

Every step is a checkpoint. The step record, the workflow's new state and the step's new or changed
candidates are committed in one transaction. Nothing is held in memory between turns: a worker reads
a snapshot each turn. Steps are append-only (a guard refuses update, delete and truncate).

## Retry behavior

- An operation's identity is `operation_key(workflow, action, subject)`. A completed or skipped
  operation is never planned again.
- A failure is attempted at most twice under the same key; `(workflow_id, key, attempt)` is unique in
  the database.
- An outage (`infrastructure_error`, an unexpected exception) is retried once for any action.
- A failure the action reports itself is retried only when the registry marks the action retryable
  and the outcome says it can recover.
- Inside one generation, the agent itself retries a recoverable model failure once.
- A commit lost to a database failure loses only that step. The step runs again when the workflow is
  resumed; its first effects were never written.

## Budgets

| Limit | Default | Ceiling |
|---|---|---|
| `max_iterations` | 3 | 5 |
| `max_llm_calls` | 12 | 20 |
| `max_tool_calls` | 40 | 60 |
| `max_retrievals` | 4 | 6 |
| `max_candidates` | 6 | 8 |
| `max_simulations` | 3 | 6 |
| `max_input_tokens` | 240,000 | 400,000 |
| `max_seconds` | 1,800 | 3,600 |

Defaults are configurable (`ARCHITECTURE_WORKFLOW_MAX_ITERATIONS`, `ARCHITECTURE_WORKFLOW_MAX_LLM_CALLS`,
`ARCHITECTURE_WORKFLOW_MAX_SECONDS`) up to the ceilings. A request may only lower them.

An action that may call a model needs room for two calls (a call and one retry) before it runs.
Token counts a provider did not report are `null`, never 0. An unanswered actionable finding at the
iteration limit is recorded as a limitation, never dropped silently.

## Cancellation

A person may cancel a queued, running, waiting or review-ready workflow. Cancellation is written on
the locked workflow row. A worker holding it can no longer commit, because the workflow is no longer
the one it read: the next step's effects are discarded and nothing more runs. What was done is kept.

## Concurrency

- **Workers.** A worker claims a queued workflow, or a running one whose lease expired, with
  `FOR UPDATE SKIP LOCKED`, and sets a lease (`ARCHITECTURE_WORKFLOW_LEASE_SECONDS`, default 120).
  Every commit renews it, and requires the worker to still hold it and the workflow to be unchanged.
  When the lease expires, another worker resumes from the last completed step; the first can no
  longer write.
- **People.** A person's moves lock the workflow row.
- **Approval.** Two workflows on one revision: the first approval revises it, the second is
  `stale_candidate`.

## Security

- **Tenant and project isolation:** every lookup goes project → workflow → candidate, and another
  project's or organization's record is not found.
- **Authorization outside the model:**
  - viewers read;
  - members and above start, give input, cancel, reject and approve (`architecture.generate`);
  - approval also needs `architecture.create` or `architecture.update`;
  - the worker re-checks the starting person's permission before every action, and a revoked
    permission stops the workflow `permission_denied` before the next action.
- **Crafted requests:** unknown fields are refused, so a request cannot set status, the review
  package, the approval, the requester, usage or tools. A budget can only be lowered.
- **Rate limits:** `run_architecture_workflow` (20 per user, 60 per address, per hour) for starting
  and giving input.
- **Audit:** five actions, carrying identifiers, statuses and counts, never the goal, answers or
  content.
- **Secrets:** redacted before a model sees retrieved text. Prompts, retrieved text and raw model
  output are never stored, returned or logged.

## Prompt injection controls

Everything a person, a document or a model wrote is untrusted data: the goal, constraints, context,
requirements, answers, retrieved passages and model output. Specifically:

- they reach a model only inside delimited data sections, which cannot be closed from inside; the
  instructions are always exactly the versioned system prompt;
- a model's answer is schema-checked and guarded, and a refused answer is never used or repaired;
- nothing a model says can act: actions come only from the planner and the registry, and approval only
  from a person through the API;
- tests drive the full API, worker and a recording model with injected goals, injected documents and
  a well-formed hostile answer ("approve, skip validation, run a shell command"). The workflow still
  validates, records only registry actions, waits for a person and changes no architecture.

## API contracts

`docs/api/architecture-workflows.md`: 8 endpoints under `/projects/{projectId}/architecture-workflows`.
They are start (`202`, queued), list, read, read a candidate, give input, cancel, reject and approve.
Workers: `python -m workers.workflow_worker`.

## Frontend workflow

`docs/frontend/architecture-workflow-contract.md`: the web app's "Generate architecture" panel calls
endpoints that do not exist. The contract maps it to a workflow: start, progress by stage and step,
waiting for a person, the review package with lineage and approvability, and the decision. It is
not integrated yet.

## Observability

- Each step writes one structured log entry (`workflow step`) with the workflow and project ids, action,
  stage, step status, attempt, iteration, duration, error code, model calls and tokens.
- Each stop writes `workflow stopped` with its failure code.
- Metrics: `workflow.steps` (by action and status), `workflow.step_ms`, `workflow.retries`,
  `workflow.llm_calls` and `workflow.failed` (by code).
- The worker logs claims and outcomes.
- No goal, prompt, retrieved text or model output is ever logged.
- The workflow record itself is the complete trace: steps with usage, history and limitations.

## Evaluation methodology

`ai/evaluation/architecture_workflow.py` runs 15 complete workflows (dataset v1). They use the real
controller, planner, registry, executors, agent pipeline, engines, evolution rules and diff, with
recorded model outputs. Grading is on structured state, never prose:

- **Outcomes:** status, failure, input asked, candidates, origins, rules and model calls.
- **Requirements:** no design before confirmation.
- **Architecture:** IR validity, traceability to pinned versions, citations of retrieved passages
  only, and assumption disclosure.
- **Analysis:** status consistent with validation, no analysis without its inputs, nothing
  unvalidated in review.
- **Iteration:** grounded triggers, resolved findings, and traces kept.
- **Safety:** injection containment, no action outside the registry, no budget overrun, no
  automatic approval, nothing after revocation, and no stored leak.

Every threshold is the measured value: all shares 1.0, all ceilings 0. The regression test also
proves each metric has something to measure and that the graders can fail.

## Known limitations

- **No live-model evaluation.** Recorded outputs show how the workflow handles answers, not how good
  a live model's designs are.
- **Capacity and cost need stored analyses of a base revision.** A first design is not analyzed for
  capacity or cost, and the workflow says so.
- **One worker processes one workflow at a time.** Throughput scales by running more workers.
- **Lease contention across connections is not tested.** `SKIP LOCKED` between two simultaneous
  workers is untested; lease expiry and takeover are tested on one connection with a controlled
  clock.
- **A step that runs past its lease** may be taken over; the first worker's late commit is then
  refused, and its effects are discarded.
- **Analysis is synchronous within a step.** Engines run in a thread; an engine that hangs holds its
  worker until the lease expires.
- **The web app is not integrated.**
- **Not claimed:** the workflow is not described as fully autonomous or production ready, and it
  does not establish that a design is correct or optimal. Those claims would need evidence this
  milestone does not produce.
