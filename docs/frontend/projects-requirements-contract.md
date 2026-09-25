# Frontend contract: projects and requirements

The backend follows the projects and requirements specification (decided in Phase 0 of that work);
`apps/web` still uses its earlier *proposed* contract and is adapted in a dedicated step. Until then
the mock API (`apps/web/api/mock`) keeps the web app working. The mismatches, and how each resolves:

| Area | `apps/web` today | Backend | Resolution in the frontend step |
|---|---|---|---|
| Project listing | `GET /projects` (all) | `GET /organizations/{orgId}/projects` (paginated `{projects, nextCursor}`) | Scope by the selected organization; follow `nextCursor` |
| Project creation | `POST /projects {name, description}` | `POST /organizations/{orgId}/projects {name, slug?, description?, settings?}` | Same, under the organization |
| Project fields | `architectureVersion`, `summary` | `slug`, `status`, `settings`, `role`, `archivedAt`, `organizationId` | Drop the two until architectures exist; add archive UI |
| Name / description limits | 80 / 500 characters | 100 / 2,000 | Keep the stricter UI limits or align |
| Requirements | One document: `GET`/`PUT /projects/{id}/requirements {description, functional[], nonFunctional{...}}` | Individual, versioned requirements (`GET`/`POST`, `PATCH` with `expectedVersion`) | Map each form field to one requirement (below); save changed ones with `PATCH` |
| Numbers | JSON numbers | Exact decimal strings in `structuredData`/`normalizedData` (numbers accepted on input) | Parse strings for display; send strings |
| Enumerations | n/a | lower-case (`capacity`, `critical`) | As is |

Form field → requirement:

| Form field | type / category | structured data |
|---|---|---|
| `peakRps` | capacity / throughput | `requests_per_second >= value requests/second` |
| `dailyActiveUsers` | capacity / daily_active_users | `daily_active_users >= value users` |
| `p99LatencyMs` | performance / latency | `latency <= value ms`, percentile 99 |
| `availabilityTarget` (0.999) | availability / availability | `availability >= value ratio` |
| `dataRetentionDays` | data / retention | `retention >= value d` |
| `regions[]` | operational / regions | `regions in [...]` |
| each `functional[]` line | functional / (a category the user picks, default `user`) | none |
| `description` | stays project-level (`PATCH /projects/{id}`) | n/a |

Unchanged and already aligned: the error envelope (`{error: {code, message, details, request_id}}`),
Bearer authentication with the in-memory access token, and camelCase JSON.
