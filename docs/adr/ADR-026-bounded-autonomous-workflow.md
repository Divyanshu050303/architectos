# ADR-026: A bounded autonomous architecture workflow, owned by the server and decided by a person

- Status: accepted
- Date: 2026-10-03

## Context

The earlier milestones built each part of architecture work separately:

- requirement extraction and pinned requirement sets;
- lexical knowledge retrieval;
- a bounded architecture agent;
- deterministic validation, capacity, cost, reliability, security, observability and simulation
  engines;
- evolution rules;
- an architecture diff;
- versioned architectures with optimistic concurrency.

A person had to call each in turn. The goal of this milestone is to carry a goal through all of them
to something a person can review, without giving any of it more authority than it has today.

Letting a model drive (choose tools, loop until satisfied, write the result) would make the outcome
depend on text the model read. That text includes the goal and project documents, which anyone with
write access can shape. It would also leave no clear point at which a person decides. The repository
had no job infrastructure: the `workers/` and orchestrator modules were empty stubs.

## Decision

### The server owns every transition

A deterministic planner picks the one next action from the workflow's own state. A controller
authorizes it, runs it and records it. A model never names an action. It is called only inside the
requirements engine and the architecture agent, as before, and its output is data: a proposal that
the deterministic code builds into IR or refuses.

### A closed registry, automatic only below canonical change

14 actions, each with a side-effect class, a permission, its stages and its retry semantics. The
workflow may run `read_only`, `analysis` and `candidate_mutation` actions on its own. Nothing in the
registry is a `canonical_mutation` or an `external_side_effect`. There is no generic code, shell,
SQL or browsing action.

### It runs as the person, checked every time

Every action runs with the starting person's permissions, checked against their current membership
before it runs. Losing access stops the workflow (`permission_denied`).

### Rules first, model second

An actionable finding is answered by a deterministic evolution rule when one applies, with no model
call. Otherwise the agent revises the candidate for that finding. Validation blocks go to the agent.
Candidates are immutable, with lineage: parent, triggering finding, rule or agent run.

### Requirements are confirmed by a person

Without a pinned set, the goal goes through the requirements engine and the workflow waits. A person
promotes candidates and pins a set; no design starts before.

### Approval through the existing versioning

A person approves exactly one reviewed candidate (its content hash, validation not blocking) through
`ArchitectureService.create` or `replace`. A base that moved on is `stale_candidate`. Approval of the
workflow, the candidate and the revision commit together.

### Postgres jobs and a worker, no new infrastructure

The `architecture_workflows` table is the queue:

- `FOR UPDATE SKIP LOCKED` claims under a lease;
- every step is a checkpoint, committed with its effects in one transaction, only while the worker
  still holds the lease and the workflow is unchanged;
- operation keys make completed steps never run again;
- a failure is retried at most once;
- budgets bound every costly action before it runs;
- cancellation is a person's write that the worker's next commit cannot overwrite.

### Everything external is untrusted

Goals, requirements, answers, retrieved passages and model output reach a model only as delimited
data. Model output is schema-checked and guarded; refused output is never used. Prompts, retrieved
text and raw output are never stored, returned or logged.

## Consequences

- **Reviewable, never self-approving.** The workflow produces a review package: candidates,
  findings, comparisons, limitations. The canonical architecture changes only on a person's approval.
  The architecture workflow is a bounded autonomous architecture engineering workflow. It proposes
  and analyzes candidate architectures; it does not determine architectural correctness, and nothing
  it produces becomes an architecture without a person's approval.
- **Less autonomy than a model-driven agent.** The workflow cannot try an approach the planner does
  not know, and it stops at budgets. Improvements are limited to what the evolution rules and the
  agent can propose for a named finding.
- **Durable and resumable.** A crashed worker loses at most the step in flight. Another worker
  resumes after the lease expires. The trace is the workflow record itself.
- **Capacity and cost need stated inputs.** Without a stored analysis of a base, they are not run,
  and the workflow says so.
- **Measured on recorded outputs.** The evaluation set shows how the workflow handles answers, not
  design quality. That would need live models and reviewers.
- **Future execution is out of scope.** Deploying, provisioning, migrating or changing anything
  outside ArchitectOS would be a separately designed subsystem with its own safety model and
  authorization. This workflow has no path to it.
