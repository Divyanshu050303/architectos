# Frontend contract: component catalog

The backend implements [docs/api/components.md](../api/components.md) and the component checks of
[validation runs](../api/validation.md). The web app has no catalog yet: its component knowledge is
hardcoded. It lives in:
- `apps/web/schemas/architecture.ts` (`COMPONENT_TYPES`, `ArchitectureNodeSchema`: `type`,
  free-text `technology`, `configuration`);
- `apps/web/types/component.ts` (`ComponentDefinition`: the palette's type, name, technology and
  default configuration);
- `apps/web/features/architecture/constants.ts` (the palette entries, e.g. `{type: "queue",
  technology: "Kafka", configuration: {partitions: 12}}`, and technology icons matched by name);
- `apps/web/features/architecture/components/NodeInspector.tsx` and `inspector/OverviewTab.tsx`.

The frontend alignment step adopts the catalog. The catalog is **not** duplicated in the web app:
it is read from the API, which is its only source of truth.

| Area | `apps/web` today | Backend | Resolution in the frontend step |
|---|---|---|---|
| Palette | A fixed list of types with a technology name and default values (e.g. Kafka with 12 partitions) | `GET /components/categories`, `GET /components?category=` — entries with `supportStatus`, `nodeKinds`, `provider`, `hosting` | Build the palette from the catalog. Show the support status; a `planned` entry says it is not specified yet. No default value that the specification does not document (the palette's `partitions: 12`, `replicas: 2` go) |
| Link to a specification | Free-text `technology` ("PostgreSQL", "Container service"), icons matched by name | A node's `component` (`databases/postgresql`) and `technology` (`postgresql`, optional version) in the Architecture IR | Set `component` when a catalog entry is chosen; keep `technology` as the IR identifier. Never infer `component` from a name |
| Configuration form | Free `configuration` record | `configuration` of the specification: IR properties with `required`, `default` (only when documented), `engines`; units in property names | Offer the specification's fields; mark required ones; show a default only when the specification states one, with its source; leave the rest empty (unknown), never zero |
| Constraints | None | `constraints` with `type`, `expected`, `severity`, `provenance`; `POST …/evaluate` gives each check's outcome | Validate while editing with `POST /components/{directory}/{entry}/evaluate`; show `violation` and `warning` with the documented expectation and remediation, `cannot_evaluate` as "not enough information" — never as passed |
| Facts about a technology | None | `capabilities`, `capacity`, `scaling`, `failureModes`, `signals`, `security`, `billing`, `operations`, each with `provenance` and `sources` | A specification view: every claim with its provenance kind (documented, inferred, unknown) and its source link and retrieval date. Signals and security properties are what the technology supports, not what the deployment does |
| Versions | None | `ref` (`id@version`), `current`, `GET …/versions`, `?version=` | Show the version a validation used (`inputs.components`), and whether it is still current |
| Validation | Findings without component checks | Rule `configuration.component-constraints`; limitations `components_not_referenced`, `catalog_unavailable` | Render these findings like the others; show the limitation (which nodes were not checked against a technology) |

Always shown with a specification: "Specifications state documented facts and their sources; they do
not guarantee the performance or capacity of a deployment." Unchanged and aligned: authentication,
the error envelope (`component_not_found`, `invalid_architecture`), and the Architecture IR node
shape (the `component` field already exists).
