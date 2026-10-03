# Frontend contract: architecture workflows

The backend is in place (`docs/api/architecture-workflows.md`). **The web app is not integrated yet.**
Its "Generate architecture" panel describes an earlier, proposed contract that the backend does not
implement:

- `apps/web/api/architectures.ts` (`generateArchitecture`);
- `apps/web/api/jobs.ts` (`getJob`);
- `apps/web/schemas/api.ts` (`JobSchema`);
- `apps/web/hooks/use-architecture.ts` (`useGenerateArchitecture`, `useJob`);
- `apps/web/features/requirements/components/GenerationPanel.tsx`.

`generateArchitecture` calls `POST /projects/{id}/architecture/generate`, and `getJob` calls
`GET /jobs/{jobId}`. Neither exists; only the mock handlers answer them. That flow also assumes a
generated architecture becomes a version on its own. In the workflow, nothing becomes an architecture
until a person approves a candidate.

This page lists what changes when the frontend is aligned. Nothing here has been built or verified in
the web app.

## Endpoints

| `apps/web` today | Backend | Change |
|---|---|---|
| `POST /projects/{id}/architecture/generate` → `Job` | `POST /projects/{id}/architecture-workflows` `{objective, constraints?, preferences?, exclusions?, context?, requirementSetId?, base?, capacityAnalysisId?, costAnalysisId?, scenario?, budget?}` → `202 Workflow` | A workflow needs a **goal in the person's words**. Without `requirementSetId`, it asks the person to confirm requirements first. |
| `GET /jobs/{jobId}` → `Job` | `GET /projects/{id}/architecture-workflows/{workflowId}` → `Workflow` | Poll the workflow while it is `queued` or `running` (every 2–5 s, backing off). It is project-scoped; there is no global job endpoint. |
| — | `GET /projects/{id}/architecture-workflows[?status=]` | New: the project's workflows, newest first. |
| — | `GET /projects/{id}/architecture-workflows/{workflowId}/candidates/{candidateId}` | New: a candidate's architecture (canonical IR), reports and lineage, for the review screen. |
| — | `POST …/{workflowId}/input` `{requirementSetId}` or `{answers}` | New: what the workflow waits for (`status = needs_input`). |
| — | `POST …/{workflowId}/cancel` | New. |
| — | `POST …/{workflowId}/reject` `{reason}` | New: the person's decision. |
| — | `POST …/{workflowId}/approve` `{candidateId, candidateContentHash, name?}` | New: the **only** way a candidate becomes an architecture or revision. |

## `Job` against `Workflow`

| Today's field | What to show instead |
|---|---|
| `status`: `queued`, `running`, `succeeded`, `failed` | `status`: `queued`, `running`, `needs_input`, `review_ready`, `approved`, `rejected`, `cancelled`, `failed`. There is no `succeeded`: the workflow's success is `review_ready`, and then a person decides. |
| `steps[]` `{id, label, status}` | `steps[]` `{ordinal, action, stage, status, attempt, error, note, usage}`. `status` is `completed`, `failed` or `skipped`, and a step is only recorded once it has ended. Show `stage` as the progress label, and `note` when a step failed or was skipped. |
| `result.architectureVersion` | **Not produced by the workflow.** After approval, the response gives `architectureId` and `revisionNumber`. |
| `error` `{code, message}` | `failure` `{code, failureClass, message, stage}`. |

## What the UI must show, and how

**Starting.** Ask for the objective in the person's words. Constraints, preferences and exclusions
are separate lists. Preferences are never constraints. Offer an existing requirement set, or none
(then the person confirms requirements later). Offer a base revision only for iterating on an
existing architecture. Show the budget as limits the person may lower.

**Progress.** Show the stage and the steps as they are recorded. Never show a percentage or an
estimated time: the server does not produce one. Show `usage` against `budget`: model calls, actions,
candidates and iterations. Token counts that are `null` are "not reported", never 0.

**Waiting for a person (`needs_input`).**

- `confirm_requirements`: link to the requirement analysis `inputNeeded.analysisId`. Let the person
  promote its candidates, pin a requirement set, and send `{requirementSetId}`.
- `clarification`: show each question with its kind and the requirements it refers to. Blocking ones
  must all be answered. Send `{answers: [{questionId, answer}]}`.

**Review (`review_ready`).** Show each candidate of `selected` with:

- its lineage: the parent, the finding it answers (`trigger`), and whether a deterministic evolution
  `rule` or the agent proposed it;
- its validation status (`blocking`) and every engine's report status. `null` blocking is "not
  validated", never "no issues";
- its `approvability`. Disable Approve when `approvable` is false, and show the `reason`.

Open a candidate for its architecture on the canvas and the engines' full reports. Show `rationale`
as the proposer's stated reasons, marked unverified. Show the workflow's `limitations` prominently:
what was not run (capacity, cost and simulation without inputs), limits reached, and knowledge not
found.

Never show a score, a ranking or a "recommended" badge. The server produces none.

**Deciding.**

- Approve sends exactly the reviewed candidate's `contentHash`. On `409
  workflow_candidate_not_approvable`, show `details.reason`. For `stale_candidate`, the base changed:
  offer to start a new workflow on the current revision.
- Reject asks for a reason.
- Cancel is available while the workflow is queued, running, waiting or ready for review.

**Access.** Viewers see workflows and candidates read-only. Hide or disable start, input, cancel,
reject and approve for them. The server refuses them anyway (`403`).

## Mock handlers

Replace the `POST /projects/:id/architecture/generate` and `GET /jobs/:jobId` handlers with handlers
for the endpoints above. A mock must never approve a candidate on its own: review and approval stay
explicit in the UI flow.
