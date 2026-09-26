# Projects

Code: `core/domain/projects/` (entities, value objects, service), `persistence/models/project.py`.
API: [docs/api/projects.md](../api/projects.md).

A **project** is an architecture initiative owned by exactly one organization (e.g. "Food Delivery
Platform"). Requirements, requirement sets and, later, architectures belong to a project.

## Rules

1. Every project belongs to exactly one organization, fixed at creation. No request can change it;
   the database enforces the foreign key (RESTRICT: an organization owning projects cannot be
   hard-deleted).
2. The creator must be a member of the organization with `project.create`, re-checked inside the
   transaction that writes.
3. **Name**: 1–100 characters after collapsing whitespace, no control or format characters.
   **Description**: at most 2,000 characters; line breaks kept.
4. **Slug**: derived from the name unless given (NFKD, ASCII only, lower-case, runs of other
   characters become one hyphen, at most 63 characters cut on a word boundary). A given slug is only
   trimmed and lower-cased, never rewritten. Unique among the organization's non-deleted projects;
   immutable after creation.
5. **Settings**: `cloud_provider` (`aws`, `gcp`, `azure`, or none) and `currency` (ISO 4217); unknown
   keys refused.

## Lifecycle

```
active ──archive──> archived ──delete──> deleted (soft)
   ^                   │
   └─────restore───────┘
```

- **Archived** projects are read-only, and so is everything under them: requirements, versions and
  requirement sets can be read but not written (`409 project_archived`). Only restore or delete.
- Archive and restore are **idempotent**: repeating one changes nothing and records nothing.
- **Delete** requires the archived state (archive first) and is a soft delete: the project is then
  not found anywhere, its slug can be reused, and nothing is purged, so requirement and architecture
  history is never lost by accident.

## Authorization

The central permission matrix (`core/domain/organizations/permissions.py`, see
[ADR-006](../adr/ADR-006-tenancy-and-authorization.md)):

| Permission | Viewer | Member | Admin | Owner |
|---|---|---|---|---|
| `project.read` | ✓ | ✓ | ✓ | ✓ |
| `project.create`, `project.update` | | ✓ | ✓ | ✓ |
| `project.archive` (archive and restore), `project.delete`, `project.policy_update` | | | ✓ | ✓ |

Every request resolves **user → membership → project's organization → permission** in one query,
inside the operation's transaction: `get project by id` is never trusted on its own. A non-member,
a deleted project and a deleted organization are all `404`.

## Architecture policy

A project has one typed **architecture policy** (`core/domain/projects/policies.py`), enforced by
the validation engine's `policy.*` rules: allowed and prohibited technologies, allowed regions, TLS
required on communicating connections, a maximum component count. Empty lists and `null` constrain
nothing; the empty policy is the default. The policy is replaced as a whole by owners and admins
(`project.policy_update`), is read-only while the project is archived, and every change is audited
as `project.policy_updated` with the names of the changed fields.

## Concurrency

Every write locks the project row (see [ADR-008](../adr/ADR-008-project-write-locking.md)). Project
changes take it exclusively, so archiving waits for requirement writes in progress and blocks new ones.
