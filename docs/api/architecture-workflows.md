# Architecture Workflows API

An architecture workflow carries a goal, step by step, to a **review package**: one or more candidate
architectures with the engines' reports. A person decides.

- **The server owns every step.** A worker claims a queued workflow from the database and asks a
  deterministic planner what comes next. Every action comes from a closed server-side registry:
  analyze requirements, ask a person, retrieve knowledge, generate an architecture, validate it, run
  the analyses its inputs allow, propose an improvement, compare, prepare the review. A model is
  called only inside the architecture agent and the requirements engine, and it never chooses an
  action.
- **It runs as you.** Each action runs with the permissions of the person who started the workflow.
  They are checked again before every action. If you lose a permission, the workflow fails
  `permission_denied`.
- **It stops for a person.**
  - Without a requirement set, the goal is analyzed by the requirements engine, and the workflow
    waits for you to confirm a requirement set (`confirm_requirements`).
  - When the agent has blocking questions, it waits for your answers (`clarification`).
- **Improvements are rules first, model second.** A candidate with a critical or high finding is
  improved first by a deterministic evolution rule. If no rule applies, the agent revises it, with
  the finding. A design that repeats an earlier one is not kept.
- **Nothing becomes an architecture until a person approves a candidate.** Approval goes through the
  architecture workflow (source `ai`).
  - It is refused unless validation evaluated the candidate with no blocking findings.
  - When the goal named a base revision, approval revises that architecture, and is refused when the
    base is no longer current (`stale_candidate`).
- **It is bounded.** A budget limits iterations, model calls, actions, retrievals, candidates,
  simulations, input tokens and wall-clock time. When a limit is reached, the workflow goes to review
  if any candidate is valid, and otherwise fails `budget_exhausted`, `timed_out` or
  `no_valid_candidate`.

Rules that hold everywhere:

- What is not known is `null`, never 0. That includes token counts a provider did not report, a
  candidate's blocking count when validation did not run, and analyses that were not run for lack of
  inputs. Missing inputs are listed in `limitations`.
- Prompts, retrieved text and a model's raw output are never stored or returned. Step outputs are
  references only: identifiers, counts and codes.
- The goal, requirements, answers and passages are untrusted data for a model, never instructions.
- Completed steps are never executed again. If a worker stops, another resumes the workflow from its
  last completed step once the first worker's lease expires.

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /projects/{projectId}/architecture-workflows` | `architecture.generate` (and `requirement.create` without a requirement set) | `WorkflowRequest` | `202 Workflow` | `404 requirement_set_not_found, architecture_not_found, architecture_revision_not_found, capacity_analysis_not_found, cost_analysis_not_found`, `409 project_archived`, `422 invalid_workflow_request, validation_error`, `429 rate_limited` |
| `GET /projects/{projectId}/architecture-workflows` | `architecture.read` | | `200 {workflows, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/architecture-workflows/{workflowId}` | `architecture.read` | | `200 Workflow` | `404 architecture_workflow_not_found` |
| `GET /projects/{projectId}/architecture-workflows/{workflowId}/candidates/{candidateId}` | `architecture.read` | | `200 WorkflowCandidate` | `404 architecture_workflow_not_found, workflow_candidate_not_found` |
| `POST /projects/{projectId}/architecture-workflows/{workflowId}/input` | `architecture.generate` | `{requirementSetId}` or `{answers}` | `200 Workflow` | `404 architecture_workflow_not_found, requirement_set_not_found`, `409 invalid_workflow_transition, project_archived`, `422 invalid_workflow_request`, `429 rate_limited` |
| `POST /projects/{projectId}/architecture-workflows/{workflowId}/cancel` | `architecture.generate` | | `200 Workflow` | `404 architecture_workflow_not_found`, `409 invalid_workflow_transition` |
| `POST /projects/{projectId}/architecture-workflows/{workflowId}/reject` | `architecture.generate` | `{reason}` | `200 Workflow` | `404 architecture_workflow_not_found`, `409 invalid_workflow_transition`, `422 invalid_workflow_request` |
| `POST /projects/{projectId}/architecture-workflows/{workflowId}/approve` | `architecture.generate` and `architecture.create` (no base) or `architecture.update` (base) | `{candidateId, candidateContentHash, name?}` | `201 {workflowId, status, candidate, architectureId, revisionNumber, createdArchitecture}` | `404 architecture_workflow_not_found, workflow_candidate_not_found, architecture_not_found`, `409 workflow_candidate_not_approvable, invalid_workflow_transition, project_archived, architecture_archived` |

On every endpoint:

- `404 project_not_found` when the project is outside your organizations.
- `403 permission_denied` without the permission.

A workflow or candidate of another project or organization is not found, which can't be told apart
from one that doesn't exist.

Who can do what:

| Role | Can |
|---|---|
| Viewer | Read workflows and candidates (`architecture.read`). |
| Member, admin, owner | Also start workflows, give input, cancel, reject and approve (`architecture.generate`). |

Starting a workflow and giving it input each let a worker run model calls. Both share one rate
limit, `run_architecture_workflow`: 20 per user and 60 per client address, per hour. The list
endpoint takes `status`, `cursor` and `limit` (1–100, default 50), newest first.

## Starting a workflow

`WorkflowRequest` (camelCase; unknown fields refused):

| Field | Meaning |
|---|---|
| `objective` | What the system should do, in your own words (1–4000 characters). |
| `constraints`, `preferences`, `exclusions` | Up to 20 each, 300 characters each. Preferences never become constraints. |
| `context` | Up to 4000 characters. |
| `requirementSetId` | Design against this pinned set. Without one, the goal (objective, constraints, preferences, exclusions, context) is analyzed by the requirements engine, as you, and the workflow waits for you to confirm a set. |
| `base` | `{architectureId, revisionNumber}`: iterate on exactly this revision. Approval then revises it. |
| `capacityAnalysisId`, `costAnalysisId` | Stored analyses of the base. Their workload and pricing are reused for every candidate. They need a `base` (`needs_base`). Without them, capacity and cost are not run. |
| `scenario` | A simulation scenario, as in the Simulations API, run on every candidate. Without one, simulation is not run. |
| `budget` | Lowers any of `maxIterations`, `maxLlmCalls`, `maxToolCalls`, `maxRetrievals`, `maxCandidates`, `maxSimulations`, `maxInputTokens`, `maxSeconds`. Raising one above the configured limit is refused (`above_configured`). |

The response is `202`. The workflow is `queued`, and nothing has run yet.

Everything the goal names must exist in the project when it is queued. The configured defaults are
3 iterations, 12 model calls, 40 actions, 4 retrievals, 6 candidates, 3 simulations, 240,000 input
tokens and 1,800 seconds (`ARCHITECTURE_WORKFLOW_MAX_*`). No configuration can exceed the domain's
ceilings.

## A workflow

| Field | Meaning |
|---|---|
| `status` | `queued`, `running`, `needs_input`, `review_ready`, `approved`, `rejected`, `cancelled` or `failed`. |
| `stage` | `intake`, `requirements`, `knowledge_retrieval`, `generation`, `validation`, `analysis`, `iteration`, `comparison`, `review` or `decision`. |
| `inputNeeded` | When `needs_input`: `kind` (`confirm_requirements` with the `analysisId` whose candidates to confirm, or `clarification` with `questions`). |
| `candidates` | Each with its lineage (`parentId`, `trigger` finding, `rule` or `agentRunId`), `status`, `contentHash`, `blocking` (null: not validated), the engines that reported, and `approvability`. |
| `steps` | Every action taken, in order: `action`, `stage`, `status` (`completed`, `failed`, `skipped`), `attempt`, `outputs` (references), `usage`, `error` and `note`. |
| `usage` | Against `budget`. |
| `selected` | The review package. |
| `approved` | `{candidateId, architectureId, revisionNumber}`. |
| `decisionReason` | Why it was rejected. |
| `failure` | `{code, failureClass, message, stage}`. |
| `history` | Every status change, with who (when a person) and when. |
| `limitations` | What was left out or could not be done. |

Failure codes: `requirements_unavailable`, `budget_exhausted`, `no_valid_candidate`,
`llm_unavailable`, `llm_timeout`, `llm_malformed_output`, `proposal_rejected`, `engine_error`,
`infrastructure_error`, `timed_out`, `permission_denied`.

Candidate statuses: `generated`, `validated`, `rejected` (validation blocks it; it is kept),
`superseded`, `selected_for_review`, `accepted`.

`WorkflowCandidate` adds the candidate's canonical `architecture` (IR), the engines' `fullReports`,
`assumptions`, `rationale` (the proposer's stated reasons, unverified), `evidence` and
`limitations`.

## Giving input, cancelling

`POST …/input` takes exactly one of the following. The workflow goes back to the queue.

- `{requirementSetId}` confirms requirements. Create the set from the analysis's candidates
  (Requirement Analyses and Requirement Sets APIs).
- `{answers: [{questionId, answer}]}` answers every blocking question (at most 50).

Giving the wrong kind of input is `422 invalid_workflow_request`. Giving input when nothing is
waiting is `409 invalid_workflow_transition`.

`POST …/cancel` stops a queued, running, waiting or review-ready workflow. A worker that holds it can
no longer record anything. What was done is kept.

## Approving, rejecting

Only a `review_ready` workflow is decided. `POST …/reject` records your reason.

`POST …/approve` names one selected candidate and its `contentHash` as you reviewed it. It creates:

- a new architecture (`createdArchitecture: true`, named `name` or the candidate's name), or
- when the goal named a base, a new revision of it.

The revision's source is `ai`, and it pins the workflow's requirement set. The approval is recorded
on the workflow and the candidate (`accepted`) in the revision's own transaction: all of it commits,
or none of it does.

`409 workflow_candidate_not_approvable` gives `details.reason`:

| Reason | Meaning |
|---|---|
| `not_selected` | The candidate is not in the review package. |
| `candidate_changed` | The content hash is not the candidate's. |
| `validation_blocking` | Validation reported blocking findings. |
| `validation_unknown` | Validation did not evaluate it. |
| `stale_candidate` | The base revision is no longer the architecture's current one (`latest_version` given). |
| `no_change` | The candidate is the base's own content. |

When two workflows on one revision are approved, the first revises it and the second is
`stale_candidate`.

## Running workers

`python -m workers.workflow_worker` runs one worker. Run as many as needed. Each claims one workflow
at a time with `FOR UPDATE SKIP LOCKED` under a lease (`ARCHITECTURE_WORKFLOW_LEASE_SECONDS`,
default 120). It advances the workflow for up to `ARCHITECTURE_WORKFLOW_WORKER_TURNS` steps (default
50), then releases it. A worker uses the same engines, model configuration and database as the API.
Without a configured model, generation fails `llm_unavailable`.

## Audit

Every person's move is recorded with identifiers, statuses and counts. The goal, answers and
architecture content are never recorded.

| Action | When |
|---|---|
| `architecture_workflow.created` | A workflow was queued. |
| `architecture_workflow.input_provided` | A person confirmed requirements or answered questions. |
| `architecture_workflow.cancelled` | A person cancelled it. |
| `architecture_workflow.rejected` | A person rejected the review package. |
| `architecture_workflow.approved` | A person approved a candidate. It is recorded with the `architecture.created` or `architecture.revised` entry of the revision. |

A worker's steps are recorded on the workflow itself (`steps`, append-only), not in the audit log.
