# Frontend contract: architecture diff

The backend is in place (`docs/api/architecture-diffs.md`). **The web app is not integrated yet.**
Three files describe an earlier, proposed contract that the backend does not implement:

- `apps/web/api/architectures.ts` (`compareVersions`);
- `apps/web/schemas/comparison.ts` (`ArchitectureComparisonSchema`);
- `apps/web/features/architecture/components/CompareVersionsDialog.tsx`.

`compareVersions` calls `GET /projects/{id}/architecture/compare`, which does not exist. Only the
mock handlers answer it.

This page lists what changes when the frontend is aligned. Nothing here has been built or verified in
the web app.

## Endpoints

| `apps/web` today | Backend | Change |
|---|---|---|
| `GET /projects/{id}/architecture/compare?from=&to=` → `ArchitectureComparison` | `POST /projects/{id}/architecture-diffs` `{base, target, requirementIds?, capacityAnalysisId?, costAnalysisId?, context?, explain?}` → `ArchitectureDiff` | A comparison is created and stored, so it is a POST. Each side is `{kind: "revision", architectureId, revisionNumber}` or `{kind: "candidate", runId}`. There is no "project architecture": name the architecture. |
| — | `GET /projects/{id}/architecture-diffs[?architectureId=]` | New: earlier comparisons, newest first, without their parts. |
| — | `GET /projects/{id}/architecture-diffs/{diffId}` | New: reopen a stored comparison. |
| — | `POST /projects/{id}/architecture-diffs/{diffId}/explanations` → `ExplanationRun` | New: ask for an explanation on demand. It is appended, and the diff never changes. |
| `GET /projects/{id}/evolution/compare` | Not this endpoint | Evolution stages are not architecture revisions. Compare the revisions they became, if any. |

## The `ArchitectureComparison` shape against an `ArchitectureDiff`

| Today's field | What to show instead |
|---|---|
| `from`, `to` `{label, version}` | `base`, `target` `{kind, architectureId, revisionNumber, runId, contentHash, label}`. |
| `components[]` `{change, nodeId, name, type, details}` | `changes[]` where `element = "node"`: `{id, elementId, change, label, kind, classes, fields, renamed}`. `change` is `added`, `removed` or `modified` (not `changed`). `renamed` marks a modified element whose name changed. |
| `connections[]` `{edgeId, sourceName, targetName}` | `changes[]` where `element = "connection"`. `endpoints` holds `[sourceId, targetId]`; resolve names from the states. Assumptions, decision references and the architecture's own fields are changes too. |
| `details[]` `{field, before, after}` | `fields[]` `{path, before, after, beforeType, afterType, category, classes, sensitivity, unit}`. `before` and `after` can be any JSON value. Show `absent` for a missing one. When `sensitivity = "secret"`, show "changed (secret)" and never a value. |
| `capacity` `{beforeMaxDailyActiveUsers, afterMaxDailyActiveUsers}` | **Not produced.** Use `engines[engine=capacity]`. When it is `not_evaluated`, show its `limitations` (no capacity analysis named) and offer to pick one. When evaluated, show `measures[]` and the bottleneck findings. Never derive a number. |
| `cost` `{before, after, currency}` | `engines[engine=cost]`: `measures[name=known_monthly_total]` when present, with its limitations (a lower bound when partial, priced at declared resources). Otherwise "Cost not compared", never `0`. |

## What the UI must show, and how

**Summary:**

- base and target labels;
- `counts` by kind and class;
- each group's `title` and `reason`;
- each engine's `status`;
- `warnings`, for example states of different architectures;
- `identical`: "No architectural changes detected."

Never show a score, a percentage better, or a winner. There is none to show.

**Graph.** Colour elements from `changes[]` by `change`, keyed by `elementId`: added, removed or
modified nodes and connections. Removed elements exist only in the base, so draw them from the base
revision's IR. The UI must not decide what was added or removed by comparing graphs itself.

**Change panel,** for a selected change:

- **What changed and the old and new state:** `fields[]`.
- **Requirement impact:** `requirements[]` whose `changeIds` include it, with the `relation`.
  Show `potential` as "verdict differs", never "violated".
- **ADRs:** `decisions[]` whose `changeIds` include it, with the note "May require review".
- **Engine impact:** the introduced and resolved `findings[]` whose `elements` include its element.
- **AI explanation:** from the latest run with `status = completed`:
  - the group's explanation and consequences;
  - statements whose `groundings` cite this change or its group;
  - risks and review questions.
- **Evidence:** the run's `evidence[]` `{chunkId, sourceId, sourceVersion, reference}`, opened with
  `GET /knowledge-sources/{sourceId}/passages/{chunkId}`.
- **Unknowns:** the explanation's `unknowns` and the diff's `unknowns`.

**Explanation states:**

- No run yet: offer "Explain".
- `not_needed`: "Nothing to explain: the states are identical."
- `failed`:
  - `llm_unavailable`: "Explanations are not configured".
  - `explanation_rejected`: "The model's answer was refused". Show the `rejections[]` codes and
    paths, which never contain its text.
  - Timeouts and budget: show the code. Offer "Try again", which appends a new run.

**Labels.** Every statement with `inferred: true` is labelled "inference, to review". A grounded
statement shows its citations as links:

| Basis | Link |
|---|---|
| `change` | the change |
| `group` | the group |
| `finding` | the finding |
| `requirement` | `REQ-n` |
| `decision` | `ADR-n` |
| `evidence` | the passage |
| `user_input` | "your note" |

**Review actions.** These may be offered: open the affected element in the workspace, open a
passage, open the requirement, and open the validation finding. Acknowledging an explanation is not
stored by the backend yet, so treat it as client-side only, or leave it for later. **No action
changes the architecture from the diff view.** There is no apply, accept, migrate or deploy action.

Don't re-implement backend rules in the UI: identity matching, classification, grouping, impact,
grounding checks and limits are the backend's.
