# Projects API

Conventions (camelCase JSON, error envelope, request ids, rate limits, Bearer tokens): see
[authentication.md](authentication.md). Every endpoint requires `Authorization: Bearer <accessToken>`.
Domain rules: [docs/domain/projects.md](../domain/projects.md).

## Endpoints

| Method and path | Permission | Body | Success | Errors |
|---|---|---|---|---|
| `GET /organizations/{orgId}/projects` | `project.read` | | `200 {projects, nextCursor}` | `422 invalid_cursor, validation_error` |
| `POST /organizations/{orgId}/projects` | `project.create` | `{name, slug?, description?, settings?}` | `201 Project` | `409 project_slug_taken`, `422 invalid_project_name, invalid_project_slug, invalid_project_description, invalid_project_settings`, `429 rate_limited` |
| `GET /projects/{projectId}` | `project.read` | | `200 Project` | |
| `PATCH /projects/{projectId}` | `project.update` | `{name?, description?, settings?}` | `200 Project` | `409 project_archived`, `422 nothing_to_update, invalid_project_*` |
| `POST /projects/{projectId}/archive` | `project.archive` | | `200 Project` (idempotent) | |
| `POST /projects/{projectId}/restore` | `project.archive` | | `200 Project` (idempotent) | |
| `DELETE /projects/{projectId}` | `project.delete` | | `204` | `409 project_not_archived` |
| `GET /projects/{projectId}/architecture-policy` | `project.read` | | `200 {projectId, policy, updatedAt}` | |
| `PUT /projects/{projectId}/architecture-policy` | `project.policy_update` | `{allowedTechnologies?, prohibitedTechnologies?, allowedRegions?, requireTls?, maxComponents?}` | `200 {projectId, policy, updatedAt}` | `409 project_archived`, `422 invalid_architecture_policy, validation_error` |

On every endpoint: **`404 project_not_found`** (or `organization_not_found` on the organization
routes) when the project does not exist, was deleted, or you are not a member of its organization:
existence is never revealed. `403 permission_denied` when your role lacks the permission.

Roles: viewers read; members also create and update; admins and owners also archive, restore,
delete and set the architecture policy.

The architecture policy is replaced as a whole (fields left out constrain nothing). Technology
names and regions are lower-case identifiers (at most 100 of each); a technology cannot be both
allowed and prohibited (`invalid_architecture_policy`, `details.reason = also_allowed`);
`maxComponents` is 1 to 1000. What each field enforces: see
[validation](../domain/projects.md#architecture-policy).

## Project

```json
{
  "id": "0199...", "organizationId": "0199...", "name": "Food Delivery Platform",
  "slug": "food-delivery-platform", "description": "", "status": "active",
  "settings": {"cloudProvider": "aws", "currency": "USD"},
  "createdByUserId": "0199...", "archivedAt": null,
  "createdAt": "2026-09-25T17:00:00Z", "updatedAt": "2026-09-25T17:00:00Z", "role": "owner"
}
```

- `slug` is derived from the name unless given; unique among the organization's non-deleted projects;
  **immutable**. A collision is `409 project_slug_taken` (no automatic suffix).
- `status` is `active` or `archived`. Archived projects are read-only (restore first).
- `settings`: `cloudProvider` (`aws`, `gcp`, `azure` or `null`) and `currency` (ISO 4217). Unknown keys
  are refused. Requirement sets snapshot these settings.
- `role` is **your** role in the project's organization.
- `organizationId` cannot be set or changed by any request body.

## Listing

`GET /organizations/{orgId}/projects?status=&search=&sort=&cursor=&limit=`

| Parameter | Values |
|---|---|
| `status` | `active` or `archived` (default: both) |
| `search` | 1–100 characters, case-insensitive, matched literally against name or slug |
| `sort` | `created_at` (newest first, default), `updated_at` (most recent first), `name` (A–Z) |
| `limit` | 1–100 (default 50) |
| `cursor` | `nextCursor` of the previous page; only valid with the sort it was issued for |

Deleted projects never appear. Pagination is keyset-based: no page skips or repeats a project when
others are created meanwhile.

## Lifecycle

`active` → archive → `archived` → restore → `active`; `archived` → delete → gone (soft delete). See
[the domain document](../domain/projects.md#lifecycle).

## Audit

`project.created` (name, slug), `project.updated` (field names), `project.archived`,
`project.restored`, `project.deleted`, `project.policy_updated` (field names, never values), in `GET /organizations/{orgId}/audit-log`.
