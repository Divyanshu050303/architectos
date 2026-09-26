# ADR-010: Many architectures per project; metadata apart from versioned content

- Status: accepted
- Date: 2026-09-26

## Context

ADR-002 introduced the Architecture IR and stored each project's single architecture as immutable
revisions. Architecture work needs more than one design per project (alternatives, evolution
stages, a discovered estate beside a target design), each named, listed, archived and eventually
deleted, and it needs to bring back an earlier revision without losing what came after. Renaming a
design is not a design change, but the IR document also has a name.

## Decision

- **Many architectures per project.** The one-per-project constraint is dropped (migration 0009);
  routes become `/projects/{id}/architectures/{architectureId}/…`. The earlier singular routes were
  removed rather than kept as aliases: no client used them (the web app still runs on its mock).
- **Metadata apart from content.** Name, description and lifecycle status belong to the
  architecture record; changing them is audited but creates no revision. Content (the IR) changes
  only through new immutable revisions. The IR keeps its own name and description as each
  snapshot's title; the record's name is the one listed. Live names are unique per project,
  ignoring case.
- **Lifecycle like projects.** active → archived (read-only, restorable) → deleted (soft, only from
  archived, owners and admins). Revisions are never deleted; a deleted architecture's name is free
  again.
- **Restore as a new revision.** Restoring revision k creates revision n+1 with k's content and
  `restored_from = k`; the current pointer never moves backwards and nothing in between changes.
- **Unchanged content creates nothing.** A save, edit or restore that leaves the content as it is
  succeeds (200, `created: false`) without a duplicate revision.
- **History is shown as stored.** A revision is returned exactly as stored, in its own schema
  version; upgrades for the engines happen on a copy in memory.
- **Secrets stay out of diffs.** Values of secret-looking settings are redacted in comparisons.

## Consequences

- Every read and write names both the project and the architecture; revisions are only ever looked
  up within an architecture already found through its project, so ids from elsewhere find nothing.
- Revision numbers are per architecture (unique `(architecture_id, number)`), allocated under a row
  lock on the architecture; concurrent edits of one revision create exactly one.
- Downgrading migration 0009 is possible only while no project has more than one architecture.
- The web app's proposed single-architecture contract must adopt the plural routes
  (docs/frontend/architecture-contract.md).
