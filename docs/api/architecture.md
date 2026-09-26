# Architecture API

Conventions: see [authentication.md](authentication.md). Every endpoint requires a Bearer token and
is reached through its project: `/projects/{projectId}/architecture`. The model itself (nodes,
connections, configuration, provenance, revisions, diff) is described in
[docs/architecture/architecture-ir.md](../architecture/architecture-ir.md).

On every endpoint: `404 project_not_found` when the project does not exist, was deleted, or you are
not a member of its organization; `404 architecture_not_found` when the project has no architecture
yet; `403 permission_denied` when your role lacks the permission. Writes on an archived project
answer `409 project_archived`.

Roles: viewers read everything (current architecture, history, comparisons); members also create,
edit and lay out the architecture.

A project has **one architecture**, whose content lives in immutable, numbered **revisions**
(`version` 1, 2, 3, …). Every edit creates a revision; nothing is overwritten.

| Method and path | Permission | Success | Errors |
|---|---|---|---|
| `POST /projects/{id}/architecture` `{ir, source?, reason?, requirementSetId?}` | `architecture.create` | `201 Architecture` | `404 requirement_set_not_found`, `409 architecture_already_exists`, `413 payload_too_large`, `422 invalid_architecture, invalid_architecture_revision, validation_error`, `429 rate_limited` |
| `GET /projects/{id}/architecture` | `architecture.read` | `200 Architecture` (current revision) | |
| `GET /projects/{id}/architecture/versions` | `architecture.read` | `200 {versions, nextCursor}` (newest first) | `422 invalid_cursor` |
| `GET /projects/{id}/architecture/versions/{version}` | `architecture.read` | `200 Architecture` | `404 architecture_revision_not_found` |
| `POST /projects/{id}/architecture/commands` `{baseVersion, commands, reason?}` | `architecture.update` | `201 Architecture + changes` | `409 architecture_version_conflict`, `422 invalid_architecture_command, invalid_architecture, architecture_unchanged, validation_error`, `429 rate_limited` |
| `PUT /projects/{id}/architecture/layout` `{positions}` | `architecture.update` | `204` | `422 invalid_architecture_layout, validation_error`, `429 rate_limited` |
| `GET /projects/{id}/architecture/compare?from={a}&to={b}` | `architecture.read` | `200 Comparison` | `404 architecture_revision_not_found` |

## Architecture

```json
{
  "id": "0199...", "projectId": "0199...", "version": 3, "currentVersion": 3, "parentVersion": 2,
  "source": "user", "summary": "1 node added (Cache); 1 node modified.", "reason": "Black Friday",
  "contentHash": "5f1c...", "irSchemaVersion": 1, "requirementSetId": "0199...",
  "createdByUserId": "0199...", "createdAt": "...",
  "ir": {"schema_version": 1, "name": "Orders", "nodes": ["..."], "connections": ["..."], "...": "..."},
  "layout": {"positions": {"api": {"x": 120, "y": 40}}, "updatedAt": "..."}
}
```

- `ir` is the **canonical Architecture IR document**, exactly as stored (snake_case, decimals as
  strings, every field present). Its JSON Schema is `core/schemas/architecture.schema.json`. It is
  the same format for creating, reading and importing, and it is never reshaped by the API.
- `version` is the revision shown; `currentVersion` the latest. Send `version` back as
  `baseVersion` when editing.
- `source`: `user`, `ai` (an approved proposal), `discovery`, `import` or `system`.
- `contentHash` is the SHA-256 of the canonical IR: two revisions with equal content have equal hashes.
- `layout` is presentation only (never versioned); a past revision shows only the positions of its
  nodes.

## Creating

`POST` takes a complete IR document (`source`: `user`, default, or `import`). The document is
validated structurally, including graph rules and requirement references (every referenced
requirement must exist in the project, at an existing version). Up to 2 MiB is accepted here (a
node is about 1 KiB); every other endpoint keeps the 64 KiB limit.

- `422 invalid_architecture`: `details.violations` lists **every** problem, each
  `{element, elementId, field, rule, message}`, e.g.
  `{"element": "connection", "elementId": "api-db", "field": "target_id", "rule": "dangling_reference", "message": "target_id 'db' is not a node of this architecture."}`.
  Rules include `required`, `unknown_field`, `invalid_id`, `invalid_kind`, `out_of_range`,
  `not_applicable`, `unknown_property`, `duplicate_id`, `dangling_reference`, `self_connection`,
  `duplicate_connection`, `boundary_not_connectable`, `containment_cycle`, `unknown_requirement`,
  `unsupported_schema_version`.
- `409 architecture_already_exists`: change it with a new revision instead.
- `422 invalid_architecture_revision`: an invalid `reason` (at most 500 characters).

## Editing

`POST .../commands` applies `commands`, in order and all or nothing, to revision `baseVersion`:

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
The response is the new revision with `changes`, the diff from its parent.

- `409 architecture_version_conflict`: `baseVersion` is not the current revision
  (`details.latestVersion`); reload and apply the edit again. Edits are never merged silently.
- `422 invalid_architecture_command`: `details: {index, command, reason, elementId}` for the first
  edit that does not fit (e.g. `unknown_node`, `duplicate_id`, `boundary_not_empty`).
- `422 invalid_architecture`: the result would not be a valid architecture (`details.violations`).
- `422 architecture_unchanged`: the edits change nothing, so no revision is created.

## Layout

`PUT .../layout` replaces the positions (`{"positions": {"api": {"x": 10, "y": 20}}}`). Positions
must name nodes of the current revision (`422 invalid_architecture_layout`,
`details: {reason: unknown_node, nodeId}`); coordinates are finite and within ±1,000,000.
Saving the layout never creates a revision and is not audited.

## Comparison

`GET .../compare?from=1&to=3` returns the deterministic diff between two revisions (either
direction). Elements are matched by id, so a rename is a modification, never a removal and an
addition.

```json
{
  "from": {"version": 1, "contentHash": "...", "createdAt": "..."},
  "to": {"version": 3, "contentHash": "...", "createdAt": "..."},
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
`cost` stay `null` until those engines exist.

## Audit

`architecture.created` and `architecture.revised` (every revision after the first) are recorded
with identifiers and counts only (project, revision, source, node and connection counts, content
digest, elements added/removed/modified), never names, descriptions or configuration.
