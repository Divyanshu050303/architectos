# Architecture API

Conventions: see [authentication.md](authentication.md). Every endpoint requires a Bearer token and
is reached through its project: `/projects/{projectId}/architectures`. The content model (nodes,
connections, configuration, provenance, revisions, diff) is described in
[docs/architecture/architecture-ir.md](../architecture/architecture-ir.md).

On every endpoint: `404 project_not_found` when the project does not exist, was deleted, or you are
not a member of its organization; `404 architecture_not_found` when the architecture does not exist,
was deleted, or belongs to another project (indistinguishable on purpose); `403 permission_denied`
when your role lacks the permission. Writes on an archived project answer `409 project_archived`;
writes on an archived architecture `409 architecture_archived`.

Roles: viewers read everything (architectures, content, history, comparisons); members also create,
edit, lay out, archive and restore; owners and admins also delete.

## Model

A project has **many architectures**. Each has:

- **metadata**: `name` (unique among the project's live architectures, ignoring case),
  `description`, `status` (`active` or `archived`). Changing metadata is audited but creates **no
  revision**;
- **content**: the Architecture IR, stored only in **immutable, numbered revisions** (1, 2, 3, …;
  never reused). The architecture points at its **current revision** (`currentVersion`); every
  content change creates a new revision, and nothing is ever overwritten. The IR keeps its own
  `name`/`description`: the design's title as of each revision;
- a **layout** (node positions): presentation only, never versioned.

| Method and path | Permission | Success | Errors |
|---|---|---|---|
| `POST /projects/{id}/architectures` `{name, description?, ir?, source?, reason?, requirementSetId?}` | `architecture.create` | `201 Architecture` | `404 requirement_set_not_found`, `409 architecture_name_taken`, `413 payload_too_large`, `422 invalid_architecture, invalid_architecture_metadata, invalid_architecture_revision, validation_error`, `429 rate_limited` |
| `GET /projects/{id}/architectures?status=&search=&cursor=&limit=` | `architecture.read` | `200 {architectures, nextCursor}` (newest first) | `422 invalid_cursor, validation_error` |
| `GET /projects/{id}/architectures/{aid}` | `architecture.read` | `200 Architecture` (current revision) | |
| `PATCH /projects/{id}/architectures/{aid}` `{name?, description?}` | `architecture.update` | `200 ArchitectureSummary` (no revision) | `409 architecture_name_taken`, `422 invalid_architecture_metadata, nothing_to_update`, `429 rate_limited` |
| `POST /projects/{id}/architectures/{aid}/archive` | `architecture.update` | `200 ArchitectureSummary` (idempotent) | |
| `POST /projects/{id}/architectures/{aid}/restore` | `architecture.update` | `200 ArchitectureSummary` (idempotent) | |
| `DELETE /projects/{id}/architectures/{aid}` | `architecture.delete` | `204` (soft delete) | `409 architecture_not_archived` |
| `PUT /projects/{id}/architectures/{aid}/content` `{baseVersion, ir, source?, reason?, requirementSetId?}` | `architecture.update` | `201` (or `200`) `Revised` | `409 architecture_version_conflict`, `413 payload_too_large`, `422 invalid_architecture, invalid_architecture_revision, validation_error`, `429 rate_limited` |
| `POST /projects/{id}/architectures/{aid}/commands` `{baseVersion, commands, reason?}` | `architecture.update` | `201` (or `200`) `Revised` | `409 architecture_version_conflict`, `422 invalid_architecture_command, invalid_architecture, validation_error`, `429 rate_limited` |
| `PUT /projects/{id}/architectures/{aid}/layout` `{positions}` | `architecture.update` | `204` | `422 invalid_architecture_layout, validation_error`, `429 rate_limited` |
| `GET /projects/{id}/architectures/{aid}/versions?cursor=&limit=` | `architecture.read` | `200 {versions, nextCursor}` (newest first) | `422 invalid_cursor` |
| `GET /projects/{id}/architectures/{aid}/versions/{version}` | `architecture.read` | `200 Architecture` (that revision) | `404 architecture_revision_not_found` |
| `POST /projects/{id}/architectures/{aid}/versions/{version}/restore` `{baseVersion, reason?}` | `architecture.update` | `201` (or `200`) `Revised` | `404 architecture_revision_not_found`, `409 architecture_version_conflict`, `429 rate_limited` |
| `GET /projects/{id}/architectures/{aid}/compare?from={a}&to={b}` | `architecture.read` | `200 Comparison` | `404 architecture_revision_not_found` |

## Architecture

```json
{
  "id": "0199...", "projectId": "0199...", "name": "Orders platform", "description": "The main design.",
  "status": "active", "currentVersion": 3, "createdByUserId": "0199...", "updatedByUserId": "0199...",
  "archivedAt": null, "createdAt": "...", "updatedAt": "...",
  "revision": {
    "version": 3, "parentVersion": 2, "restoredFromVersion": null, "source": "user",
    "summary": "1 node added (Cache); 1 node modified.", "reason": "Black Friday",
    "contentHash": "5f1c...", "irSchemaVersion": 1, "requirementSetId": "0199...",
    "createdByUserId": "0199...", "createdAt": "..."
  },
  "ir": {"schema_version": 1, "name": "Orders", "nodes": ["..."], "connections": ["..."], "...": "..."},
  "layout": {"positions": {"api": {"x": 120, "y": 40}}, "updatedAt": "..."}
}
```

- `ir` is the revision's **canonical Architecture IR document exactly as stored**, in the schema
  version it was stored in (`revision.irSchemaVersion`); history is never transformed when read.
  Its JSON Schema is `core/schemas/architecture.schema.json`.
- Listings (`ArchitectureSummary`) carry the metadata only, never the content.
- `revision.source`: `user`, `ai` (an approved proposal), `discovery`, `import` or `system`.
- `revision.contentHash` is the SHA-256 of the canonical IR: equal content, equal hash.
- `layout` shows only the positions of the revision's nodes.

## Creating

`POST` creates the architecture at revision 1 with the given IR document, or with an **empty
architecture** (no nodes, the IR titled with the name) when `ir` is omitted. The document is
validated structurally, including graph rules and requirement references (every referenced
requirement must exist in the project, at an existing version). Up to 2 MiB is accepted when
creating and when saving content (a node is about 1 KiB); every other endpoint keeps 64 KiB.

- `422 invalid_architecture`: `details.violations` lists **every** problem, each
  `{element, elementId, field, rule, message}`, e.g.
  `{"element": "connection", "elementId": "api-db", "field": "target_id", "rule": "dangling_reference", "message": "target_id 'db' is not a node of this architecture."}`.
  Rules include `required`, `unknown_field`, `invalid_id`, `invalid_kind`, `out_of_range`,
  `not_applicable`, `unknown_property`, `duplicate_id`, `dangling_reference`, `self_connection`,
  `duplicate_connection`, `boundary_not_connectable`, `containment_cycle`, `unknown_requirement`,
  `unsupported_schema_version`.
- `422 invalid_architecture_metadata`: `details: {field, reason}` for an empty or over-long name
  (1-100 characters) or description (up to 2,000), or control characters.
- `422 invalid_architecture_revision`: an invalid `reason` (at most 500 characters).
- `409 architecture_name_taken`: another live architecture of the project has this name.

## Metadata versus content

| Change | How | Revision | Audit |
|---|---|---|---|
| Name, description | `PATCH` | none | `architecture.updated` (changed field names only) |
| Archive, restore, delete | `POST …/archive`, `POST …/restore`, `DELETE` | none | `architecture.archived`, `.restored`, `.deleted` |
| New content | `PUT …/content`, `POST …/commands`, `POST …/versions/{n}/restore` | a new one | `architecture.revised`, `architecture.revision_restored` |
| Layout | `PUT …/layout` | none | none (presentation) |

## Saving content

Every content change is based on the current revision the client last saw, `baseVersion`:

- `409 architecture_version_conflict` (`details.latestVersion`) when it is no longer current: reload
  and apply the change again. Content is never merged or overwritten silently (no last write wins).
- A change that leaves the content as it is creates **no revision**: `200` with `created: false`,
  the current revision and empty `changes`. A change that does create one answers `201`,
  `created: true`, with `changes` (the diff from its parent).

`PUT …/content` replaces the whole IR (`source`: `user` or `import`). `POST …/commands` applies
edits, in order and all or nothing:

| `type` | Fields | Effect |
|---|---|---|
| `add_node` | `node` (an IR node) | Adds a node; its id must be new |
| `remove_nodes` | `nodeIds` | Removes nodes and their connections; a boundary must be empty (or removed with its contents) |
| `add_connection` | `connection` (an IR connection) | Adds a connection between existing nodes |
| `remove_connections` | `connectionIds` | Removes connections |
| `rename_node` | `nodeId`, `name` | Renames; the node keeps its id |
| `update_configuration` | `nodeId`, `values` | Sets known properties (`null` clears one); an unknown value becomes known |
| `change_replicas` | `nodeId`, `replicas` | Sets `replicas` |

Every field an edit changes is attributed to the editor (`field_provenance`, source `user_edit`).

- `422 invalid_architecture_command`: `details: {index, command, reason, elementId}` for the first
  edit that does not fit (e.g. `unknown_node`, `duplicate_id`, `boundary_not_empty`).
- `422 invalid_architecture`: the result would not be a valid architecture (`details.violations`).
- `architecture_unchanged` is never answered: an unchanged save succeeds without a revision.

## History and restore

`GET …/versions` lists revisions newest first without their content; each item has `current` (is it
the current revision) and `restoredFromVersion`. `GET …/versions/{n}` returns one revision exactly
as stored.

`POST …/versions/{n}/restore` with `{baseVersion}` (the current revision) **creates a new revision**
whose content is revision `n`'s: with revisions 1, 2, 3 (current), restoring 1 creates revision 4
with 1's content and `restoredFromVersion: 1`; revisions 1, 2 and 3 are unchanged, and the current
pointer never moves backwards. Restored content is validated like any other (a requirement it
references must still exist). Restoring content equal to the current one creates nothing (`200`).

## Lifecycle

`active` → `archived` (read-only; content, metadata and layout refuse changes with
`409 architecture_archived`; everything stays readable) → back to `active` with `…/restore`, or
**deleted** (`DELETE`, only from `archived`, owners and admins). Deletion is soft: the architecture is
no longer found anywhere and its name is free again, but its revisions are kept; nothing is purged.
Archiving the project freezes all its architectures.

## Layout

`PUT …/layout` replaces the positions (`{"positions": {"api": {"x": 10, "y": 20}}}`). Positions
must name nodes of the current revision (`422 invalid_architecture_layout`,
`details: {reason: unknown_node, nodeId}`); coordinates are finite and within ±1,000,000.

## Comparison

`GET …/compare?from=1&to=3` returns the deterministic diff between two revisions **of this
architecture** (either direction; a revision number of another architecture is not found here).
Elements are matched by id, so a rename is a modification, never a removal and an addition.
Values of settings that look like secrets (`password`, `token`, `api_key`, …) are `"[redacted]"`:
the change is reported, the values are not.

```json
{
  "from": {"version": 1, "contentHash": "...", "irSchemaVersion": 1, "createdAt": "..."},
  "to": {"version": 3, "contentHash": "...", "irSchemaVersion": 1, "createdAt": "..."},
  "summary": "1 node added (Cache); 1 node modified.",
  "architecture": [],
  "nodes": [{"element": "node", "elementId": "api", "change": "modified", "label": "Orders API", "kind": "service",
             "categories": ["resources"],
             "fields": [{"field": "configuration.replicas", "before": 3, "after": 6, "category": "resources"}]}],
  "connections": [], "assumptions": [], "decisions": [],
  "capacity": null, "cost": null
}
```

Field categories: `description`, `kind`, `technology`, `resources`, `configuration`, `placement`,
`lifecycle`, `endpoints`, `semantics`, `metadata`, `provenance`, `traceability`. `capacity` and
`cost` stay `null` until those engines exist. The diff is structural: it says what changed, not
whether two different structures behave the same.

## Audit

`architecture.created`, `architecture.updated`, `architecture.revised`,
`architecture.revision_restored`, `architecture.archived`, `architecture.restored` and
`architecture.deleted` are recorded with identifiers and counts only (project, revision, source,
node and connection counts, content digest, elements added/removed/modified, changed field names,
the restored revision), never names, descriptions or configuration.
