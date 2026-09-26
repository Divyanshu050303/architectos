# Frontend contract: architecture

The backend implements the Architecture IR ([docs/architecture/architecture-ir.md](../architecture/architecture-ir.md))
and the endpoints of [docs/api/architecture.md](../api/architecture.md), following the endpoint list
`apps/web` proposed (`apps/web/api/architectures.ts`). The web app still uses its proposed shapes,
served by its mock API; the frontend alignment step adopts the backend's. The differences, and how
each resolves:

| Area | `apps/web` today | Backend | Resolution in the frontend step |
|---|---|---|---|
| Architecture body | `{id, projectId, version, nodes, edges, assumptions, createdAt, createdBy}` | Envelope `{id, projectId, version, currentVersion, source, summary, contentHash, …, ir, layout}` with the IR document in `ir` | Read `ir.nodes`, `ir.connections`, `ir.assumptions`; `createdBy` becomes `source` |
| Node | `{id, type, name, technology, description?, domain?, configuration, position}` | `{id, kind, name, technology: {name, version}, description, component, parent_id, configuration: {values, unknown, extra}, provenance, field_provenance, …}` | `type` → `kind`; show `technology.name` (+ version); `domain` → `metadata.domain`; positions from `layout.positions`; show unknown values and provenance |
| Node kinds | 12 kinds | The same 12 plus `boundary` (system, network, region, account, cluster, trust zone) | Render boundaries as groups (`parent_id`) |
| Edge | `{id, source, target, protocol?, label?, synchronous, critical}` | Connection `{id, source_id, target_id, kind, protocol, interaction, bidirectional, critical, name, description, …}` | `source/target` → `source_id/target_id`; `label` → `name`; `synchronous` → `interaction`; add `kind` (request, publish, consume, data_access, replication, dependency) |
| Configuration | `Record<string, unknown>` with numbers | Known properties with canonical units (`memory_limit_bytes`); decimals as strings; unrecognized settings in `extra` | Edit known properties with typed inputs; show `extra` read-only |
| Assumption | `{id, statement, source: user/ai/default}` | `{id, statement, provenance, subject_ids, requirement_refs}` | Source from `provenance.source` |
| Versions | `GET …/versions` → array | `{versions, nextCursor}` (newest first) | Follow `nextCursor` |
| Commands | `ADD_COMPONENT`, `REMOVE_COMPONENTS`, `CONNECT_COMPONENTS`, `REMOVE_CONNECTIONS`, `RENAME_COMPONENT`, `UPDATE_CONFIGURATION`, `CHANGE_REPLICAS`, `MOVE_COMPONENTS` | `add_node`, `remove_nodes`, `add_connection`, `remove_connections`, `rename_node`, `update_configuration`, `change_replicas` | Map one to one; `MOVE_COMPONENTS` goes to `PUT …/layout` only (as today) |
| Save response | New `Architecture` | `201` new revision + `changes` (the diff) | Use `changes` to highlight what changed |
| Conflict | `409 version_conflict`, `{latestVersion}` | `409 architecture_version_conflict`, `{latestVersion}` | Match the new code |
| Layout | `PUT …/layout {baseVersion, positions}` | `PUT …/layout {positions}` (no revision) | Drop `baseVersion` |
| Compare | `{from: {label, version}, to, components[], connections[], capacity, cost}` | `{from: {version, contentHash, createdAt}, to, summary, nodes[], connections[], architecture[], assumptions[], decisions[], capacity: null, cost: null}` | `components` → `nodes` (`elementId`, `label`, `kind`, `fields`); names of connection endpoints from the IR; capacity and cost stay null until those engines exist |
| Generate | `POST …/architecture/generate` → Job | Not implemented (Architecture Engine specification) | Keep hidden until the engine exists |

Unchanged and aligned: authentication, the error envelope, project scoping (`404
project_not_found` outside the organization), and one architecture per project.
