# Architecture Agent API

The architecture agent **proposes** an architecture for a requirement set. A person decides:

- **The model proposes data.** It is called at one fixed stage, at most twice (the call and one retry
  of a failure that can recover). It never chooses what happens next, and it never writes an
  architecture.
- **The candidate is canonical Architecture IR**, built and checked deterministically. Every element
  has `llm_proposal` provenance with the model's stated confidence. Nothing in it is verified.
- **The engines report on it.** Validation, reliability, security and observability run on the
  candidate. Capacity, cost and simulation are reported as `not_evaluated`, with why.
- **Nothing becomes an architecture until a person accepts the candidate.** Acceptance goes through
  the architecture workflow (source `ai`), and is refused while validation reports blocking findings.

Rules that hold everywhere:

- What is not known is `null`, never 0: token counts a provider did not report, and cost (provider
  pricing is not configured).
- Prompts, retrieved text and the model's raw output are never stored or returned. Only the
  output's SHA-256 and size are kept.
- Everything a person or a document wrote (objective, requirements, answers, passages, a base
  architecture) is given to the model as delimited, untrusted data, never as instructions.
- Without a configured model (`ARCHITECTURE_AGENT_LLM_PROVIDER=none`, the default), a run that
  reaches the proposal stage fails `llm_unavailable`. Nothing is sent anywhere, and no placeholder
  design is produced.

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /projects/{projectId}/architecture-agent-runs` | `architecture.generate` | `AgentRunRequest` | `201 AgentRun` | `404 requirement_set_not_found, architecture_not_found, architecture_revision_not_found`, `409 project_archived`, `422 invalid_agent_request, validation_error`, `429 rate_limited` |
| `GET /projects/{projectId}/architecture-agent-runs` | `architecture.read` | | `200 {runs, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/architecture-agent-runs/{runId}` | `architecture.read` | | `200 AgentRun` | `404 agent_run_not_found` |
| `POST /projects/{projectId}/architecture-agent-runs/{runId}/answers` | `architecture.generate` | `AnswersRequest` | `200 AgentRun` | `404 agent_run_not_found, requirement_set_not_found`, `409 invalid_agent_transition, project_archived`, `422 invalid_agent_request`, `429 rate_limited` |
| `POST /projects/{projectId}/architecture-agent-runs/{runId}/cancel` | `architecture.generate` | | `200 AgentRun` | `404 agent_run_not_found`, `409 invalid_agent_transition` |
| `POST /projects/{projectId}/architecture-agent-runs/{runId}/reject` | `architecture.generate` | `{reason}` | `200 AgentRun` | `404 agent_run_not_found`, `409 invalid_agent_transition`, `422 invalid_agent_request` |
| `POST /projects/{projectId}/architecture-agent-runs/{runId}/accept` | `architecture.generate` and `architecture.create` (new) or `architecture.update` (iteration) | `{candidateContentHash, name?}` | `201 {run, architectureId, revisionNumber, createdArchitecture}` | `404 agent_run_not_found, architecture_not_found`, `409 agent_candidate_not_acceptable, architecture_version_conflict, invalid_agent_transition, project_archived, architecture_archived` |

On every endpoint:

- `404 project_not_found` when the project is outside your organizations.
- `403 permission_denied` without the permission.

A run of another project or organization is not found, which can't be told apart from one that
doesn't exist.

Who can do what:

| Role | Can |
|---|---|
| Viewer | Read runs (`architecture.read`). |
| Member, admin, owner | Also start runs, answer, cancel, reject and accept (`architecture.generate`). |

Starting a run and answering one each run a pass that may call the model. Both share one rate limit,
`run_architecture_agent`: 30 per user and 60 per client address, per hour. The list endpoint takes
`status`, `cursor` and `limit` (1–100, default 50), newest first.

## Starting a run

`AgentRunRequest` (camelCase; unknown fields refused):

| Field | Meaning |
|---|---|
| `requirementSetId` | The requirement set the design is made against (required). Its pinned versions are what the candidate is traced to. |
| `objective` | What the system should do, in your own words (required, up to 4,000 characters). |
| `constraints`, `preferences`, `exclusions` | Up to 20 items each, up to 300 characters. Kept as written and apart: a preference never becomes a constraint. |
| `context` | Anything else to consider (up to 4,000 characters). |
| `base` | `{architectureId, revisionNumber}`: iterate on exactly this revision. |
| `budget` | Lowers the run's limits, never raises them: `maxModelCalls` (≤ 2), `maxInputTokens` (≤ 60,000), `maxOutputTokens` (≤ 8,000), `maxSeconds` (≤ 90), `maxPassages` (≤ 20), `maxContextChars` (≤ 60,000). |

An omitted value is not a default: no scale, availability, budget or technology is assumed because it
was left out.

A pass runs these stages, in this order:

1. **Interpretation.** The requirements engine's analysis of the set:
   - A conflict, or no requirement about traffic or availability, is **blocking**. The run stops at
     `awaiting_clarification` with its questions, and the model is not called.
   - A vague requirement, a metric without a lower bound, or another uncovered concern is asked
     without blocking.
2. **Retrieval.** Project knowledge, through the knowledge retriever (authorized and cited). If
   retrieval fails, that is stated in `limitations` and the run goes on without passages.
3. **Context.** Bounded by the budget. The requirements and your own words are never cut; passages,
   then the catalog listing, give way, and what was left out is stated.
4. **Proposal.** One structured model call. Only a timeout, a temporary provider failure or output
   that does not follow the schema is retried, once.
5. **Construction.** The proposal becomes canonical IR, or is refused with every reason
   (`rejections`). It is never repaired. A component must be in the catalog and not deprecated; a
   requirement or passage cited must be one the context listed.
6. **Validation** (required) and **analysis** (reliability, security, observability).

The run is stored when the pass ends, as `awaiting_clarification`, `candidate_ready` or `failed`.

## Answering, cancelling

`AnswersRequest` is `{answers: [{questionId, answer}]}` (1–50 answers, each up to 1,000 characters).
An unknown question, or a blocking question still unanswered, is `422 invalid_agent_request`
(`details.reason`: `unknown_question`, `blocking_questions_unanswered`). Once every blocking question
is answered, the same run continues, with the answers given to the model as your statements. Answers
racing each other: the second gets `409 invalid_agent_transition`.

Only a run awaiting clarification can be cancelled.

## A run

| Field | Meaning |
|---|---|
| `status` | `awaiting_clarification`, `candidate_ready`, `failed`, `cancelled`, `accepted` or `rejected` (a stored run is never `queued` or `running`). |
| `stage` | Where it is or stopped: `intake`, `interpretation`, `retrieval`, `context`, `proposal`, `construction`, `validation`, `analysis`, `review`, `decision`. |
| `questions` | `{id, kind, question, requirementRefs, blocking, answered}`. `kind` is `conflict`, `missing_concern`, `ambiguity`, `unbounded` or `proposer` (the model's own questions; never blocking). |
| `proposal` | The model's proposal as validated: nodes, connections, decisions (alternatives, trade-offs), claims, risks and questions. Every claim has a `basis`: `proposed`, `assumption`, `retrieved` (it cites a passage), `unknown` or `unsupported`. `user_provided` and `estimate` come only from people and engines. |
| `candidate` | `{contentHash, architecture, normalizations, evidence, uncoveredRequirements}`. `architecture` is canonical IR; `evidence` is the cited passages with their citations; `uncoveredRequirements` are the set's requirements no element traces to. |
| `reports` | One per engine: `{engine, status, versions, findings, summary, limitations, error}`. `status` is `evaluated`, `not_evaluated` (with why) or `failed` (`error: engine_error`, no findings: an absence of findings means nothing). `summary` is the engine's own counts, never a score. |
| `acceptability` | `{acceptable, reason}`. `reason` is `validation_blocking`, `validation_unknown` or `validation_missing`, or the run's status when it is not `candidate_ready`. |
| `rejections` | `{code, path, detail}`: why the output could not be read, or why the proposal cannot become an architecture. |
| `usage` | `{modelCalls, inputTokens, outputTokens, modelLatencyMs, retrievalCalls, engineRuns, cost: null}`. |
| `model`, `promptVersion`, `rawOutput` | The provider/model, the prompt version, and `{sha256, bytes}` of the last output received. |
| `failure` | `{code, message, stage}`. `code` is one of `llm_unavailable`, `llm_timeout`, `llm_malformed_output`, `proposal_rejected`, `budget_exhausted`, `requirements_unusable`, `engine_error`. The message never repeats a provider's error. |
| `accepted`, `decisionReason` | The revision the candidate became, or why it was rejected. |
| `history` | Every status change: `{status, stage, at, userId}` (`userId` when a person made it). |
| `limitations` | What the run left out or could not do: passages left out of the context, retrieval that failed, pinned requirements that can no longer be read. |

## Accepting, rejecting

`POST .../accept` takes `{candidateContentHash, name?}`:

- **No base:** a new architecture (named `name`, or the candidate's name) whose revision 1 is the
  candidate.
- **An iteration:** a new revision of the base architecture, which must still be at the base
  revision (otherwise `409 architecture_version_conflict`).

`409 agent_candidate_not_acceptable` gives `details.reason`:

| Reason | Meaning |
|---|---|
| `candidate_changed` | The hash is not the candidate's. |
| `validation_blocking` | Validation reports blocking findings. |
| `validation_unknown`, `validation_missing` | Validation did not establish that nothing blocks. |
| `no_change` | The candidate is the base's own content. |

The acceptance is recorded on the run in the revision's own transaction: both commit, or neither
does. An accepted run is kept: it is the provenance of its revision.

`POST .../reject` takes `{reason}` (1–2,000 characters). The run is kept as it was reviewed.

## Audit

| Action | When |
|---|---|
| `agent_run.created` | A run was started and its first pass stored (waiting, a candidate, or failed). |
| `agent_run.answered` | A person answered its questions and the run went on. |
| `agent_run.cancelled` | A person cancelled a run waiting for answers. |
| `agent_run.rejected` | A person rejected the candidate. |
| `agent_run.accepted` | A person accepted the candidate as a revision. |

Entries carry identifiers, statuses and counts: `project_id`, `requirement_set_id`, `status`,
`stage`, `model_calls`, `questions`, `failure`, `prompt_version`, plus the number of answers or the
architecture and revision created. They never carry the objective, answers, prompts, passages,
proposal text or model output.
