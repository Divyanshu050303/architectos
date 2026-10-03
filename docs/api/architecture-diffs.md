# Architecture Diff API

An architecture diff compares **two exact states** of a project's architecture and says what
changed, what that touches, and what the engines find in each state. A person reads it and decides.

- **The diff is deterministic.** Elements are matched by their stable id. Every change is in exactly
  one rule-formed group, and the group's `reason` says why its changes are together. The same two
  states always give the same changes, ids and groups.
- **Impact is established, never guessed.**
  - Requirements are related through the architecture's own traces and the validation engine's
    verdicts, never by their wording.
  - ADRs whose elements changed *may require review*. Nothing more is claimed.
  - Validation, reliability, security and observability run on both states. Findings are compared
    by stable id: introduced and resolved findings are listed, unchanged ones are counted.
  - Capacity and cost are compared only on a stored analysis you name. Otherwise they are
    `not_evaluated`, with why.
- **No score, no winner, no recommendation to deploy.** Counts are counts.
- **An explanation is optional, separate and appended.** The model explains the stored diff. Every
  statement cites what it rests on, or is labelled an inference. The diff itself never changes.
- **Nothing here changes an architecture, a revision, an agent run or an analysis.**

Rules that hold everywhere:

- A state is a **revision** (`{kind: "revision", architectureId, revisionNumber}`) or an
  **architecture agent run's candidate** (`{kind: "candidate", runId}`). It is never "the latest".
- **A secret's values are never stored or returned.** A configuration or metadata key that looks
  like a credential is reported as changed, with type `redacted` and `null` values.
- **The same answer for every unavailable state.** A state that is missing, in another project or
  organization, or not comparable (a run without a candidate) gets `404 compared_state_not_found`
  with `details.side`.
- **What is not known is `null` or said in `limitations` or `unknowns`, never 0.** That covers token
  counts a provider did not report, a measure only one state has, and an engine without its inputs.
- **Nothing the model saw is stored.** Prompts, retrieved text and the model's raw output are never
  stored or returned; only the output's SHA-256 and size are kept.
- **Without a configured model** (`ARCHITECTURE_DIFF_LLM_PROVIDER=none`, the default), an
  explanation fails `llm_unavailable`. Nothing is sent anywhere, and the diff is unaffected.

| Endpoint | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `POST /projects/{projectId}/architecture-diffs` | `architecture.analyze` | `DiffCreateRequest` | `201 ArchitectureDiff` | `404 compared_state_not_found, capacity_analysis_not_found, cost_analysis_not_found, pricing_snapshot_not_found`, `409 project_archived`, `422 invalid_diff_request, architecture_diff_too_large, validation_error`, `429 rate_limited` |
| `GET /projects/{projectId}/architecture-diffs` | `architecture.read` | | `200 {diffs, nextCursor}` | `422 invalid_cursor` |
| `GET /projects/{projectId}/architecture-diffs/{diffId}` | `architecture.read` | | `200 ArchitectureDiff` | `404 architecture_diff_not_found` |
| `POST /projects/{projectId}/architecture-diffs/{diffId}/explanations` | `architecture.analyze` | | `201 ExplanationRun` | `404 architecture_diff_not_found`, `409 project_archived`, `429 rate_limited` |

On every endpoint:

- `404 project_not_found` when the project is outside your organizations.
- `403 permission_denied` without the permission.

A diff of another project or organization is not found, which can't be told apart from one that
doesn't exist.

| Role | Can |
|---|---|
| Viewer | Read diffs and their explanations (`architecture.read`). |
| Member, admin, owner | Also compare and ask for explanations (`architecture.analyze`). |

Rate limits:

- `compare_architectures`: 120 comparisons per user per hour.
- `explain_architecture_diff`: 30 per user and 60 per client address per hour. A comparison with
  `explain: true` counts against both.

The list endpoint is newest first. It takes `architectureId` (diffs with a revision of that
architecture on either side), `cursor` and `limit` (1–100, default 50).

## Comparing

`DiffCreateRequest` (camelCase; unknown fields refused):

| Field | Meaning |
|---|---|
| `base`, `target` | The two states. They must differ. |
| `requirementIds` | Up to 200 requirements to report on. Each gets a relation, including `no_relationship` or `undetermined`. Omitted: the requirements the changes touch. |
| `capacityAnalysisId` | A stored capacity analysis of a compared architecture. Its workload compares capacity in both states. |
| `costAnalysisId` | A stored cost analysis of a compared architecture. Its pricing snapshot, pricing date and hours price both states at their declared resources. |
| `context` | Up to 2,000 characters of your own words about the change. They are given to the explanation as data, and cited as `user_input`. |
| `explain` | Also append one explanation run now. Default `false`. |

States of different architectures are compared by element id alone; a warning says so.

## A diff

| Field | Meaning |
|---|---|
| `base`, `target` | Each state: its reference, `contentHash` and `label`. |
| `identical` | Whether the two states have the same content (no changes). |
| `counts` | Changes by kind (`added`, `removed`, `modified`) and by class (`class:scaling`…). |
| `changes` | See the first list below. |
| `groups` | `{id, rule, title, reason, changeIds, elementIds}`. Every change is in exactly one group. |
| `requirements` | See the second list below. |
| `decisions` | ADRs (proposed or accepted) whose elements changed, with the note "May require review". |
| `engines` | See the third list below. |
| `versions` | The rules that produced the diff. |
| `warnings`, `unknowns` | What could not be established, said. |
| `explanations` | Every explanation run, oldest first. |

Each entry in `changes` is `{id, element, elementId, change, label, kind, classes, fields, renamed,
endpoints}`:

- `classes` say what a change *concerns* (`scaling`, `security`…), never what it does.
- Each field is `{path, before, after, beforeType, afterType, category, classes, sensitivity, unit}`.

Each entry in `requirements` has these fields:

- `relation` is one of `directly_changed`, `element_changed`, `potential` (the validation verdicts
  differ), `no_relationship` or `undetermined`.
- `baseVerdict` and `targetVerdict` are the validation engine's verdicts, or `null` when not
  evaluated.

Each entry in `engines` is one engine's comparison:

- `status` is `evaluated`, `not_evaluated` or `failed`.
- `findings` lists the findings introduced and resolved. `unchanged` counts the rest.
- `baseSummary` and `targetSummary` are each state's own counts.
- `measures` holds only values the engine produced in both states.
- `limitations` and `error` say what went wrong or what is missing.

## Explanations

An explanation run is `{id, diffId, status, model, promptVersion, usage, explanation, evidence,
failure, rejections, limitations}`.

`status` is one of these:

- `completed`: the explanation passed every check.
- `failed`: see `failure`, one of `llm_unavailable`, `llm_timeout`, `llm_malformed_output`,
  `explanation_rejected` or `budget_exhausted`.
- `not_needed`: the states are identical. The model is not called.

An explanation is
`{summary, groups[{groupId, title, explanation, consequences, unknowns}], tradeoffs,
requirements[{reference, explanation}], risks, questions, unknowns}`.

Each statement is `{text, groundings[{basis, ref}], inferred}`:

- `basis` is one of `change`, `group`, `finding`, `requirement`, `decision`, `evidence` or
  `user_input`.
- `inferred: true` marks a hypothesis to review, never a fact.

An output is refused whole, never repaired, when it:

- cites something it was not given;
- explains a group or requirement that is not in the diff;
- states a number the data does not;
- uses score, rating or winner language;
- asks a review question that does not ask;
- contains a URL, an IP address or a credential.

`rejections` say where it failed, never what it said. `evidence` lists the citations of the
passages an explanation cites.

## Audit

Every comparison and explanation is recorded with identifiers, statuses and counts only. Your
context, architecture content, prompts, passages and model output are never recorded.

| Action | When |
|---|---|
| `architecture_diff.created` | Two states were compared and the diff stored. |
| `architecture_diff.explained` | An explanation run was appended to a diff, whatever its status. |
