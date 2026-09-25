# ADR-008: Project lifecycle and write locking

- Status: accepted
- Date: 2026-09-25

## Context

Archived projects must be read-only, including everything under them. Requirement numbers must be
allocated without gaps or races, and a requirement set must be a consistent snapshot. At the same time
several people edit requirements of the same project at once.

## Decision

- **Lifecycle**: `active → archived → deleted`, archive first then soft delete; nothing is purged.
  Archive and restore are idempotent. See [docs/domain/projects.md](../domain/projects.md).
- **Every write under a project locks the project row** inside its transaction, after resolving the
  caller's membership in the same query (`ProjectLock`):
  - **SHARE** (`FOR SHARE`) for editing or deleting one requirement: such writes run in parallel.
  - **EXCLUSIVE** (`FOR UPDATE`) for project changes (update, archive, restore, delete), creating a
    requirement (number allocation) and creating a requirement set (consistent snapshot).

  Under either lock the project must be modifiable, so an archive either happens before a write
  (which then sees `project_archived`) or waits for it to finish.
- The requirement row itself is locked `FOR UPDATE` for changes, and saving a version is guarded on
  the previous version number, so a lost update is impossible even without the locks.

## Consequences

- Requirement creation and set creation in one project are serialized (short transactions; human
  rates). Edits of different requirements are not.
- Lock modes are asserted by `tests/integration/api/test_project_locks.py`.
- Indexes serve every hot query (asserted by `tests/integration/database/test_query_plans.py` against
  realistic data), including the project listing index `(organization_id, created_at, id)`, which
  replaced a key with `status` in the middle that could not deliver the default order.
