# Observability engine

Deterministic, explainable, **architecture-level** observability analysis of an architecture
revision: which components the architecture declares logs, metrics, traces, health checks and alert
rules for, whether that telemetry has a modeled path to an observability component, whether request
flows propagate the trace context, whether alerts have a modeled way out, and whether the project's
observability policy and SLO and monitoring requirements are traceable to what is declared.

- The engine **analyzes architecture-level observability configuration**: what the architecture
  **declares**.
- It **does not collect or query live telemetry**: no metric, log line, trace or alert is read, from
  anywhere.
- It **does not prove instrumentation is functioning**: a declared capability is what the
  architecture states, not evidence that telemetry is emitted, collected, retained, queryable or
  acted on.
- It **does not calculate SLO attainment**: without runtime measurements, an objective can only be
  traced to a measurable and alerted indicator, never reported as met.
- **Findings require engineering review.** Recommendations are options; nothing is changed
  automatically. There is no score, no percentage and no maturity level.

No language model takes part; the same revision, request, policy and requirements always give the
same result and `resultFingerprint`.

Code: `core/domain/observability/` (values, inputs, results, request and lifecycle, queries, report,
service), `engines/observability/` (context, orchestrator, analyzers, conditions), `persistence/`
(migration 0017), `apps/api/routes/observability.py`. API:
[docs/api/observability.md](../api/observability.md). Decision:
[ADR-016](../adr/ADR-016-deterministic-observability.md). Frontend:
[observability contract](../frontend/observability-contract.md).

## Purpose and scope

It helps answer: which components are declared critical; which of them model logs, metrics, traces,
health checks and alert rules; which declared telemetry has no modeled collection path; where a
request's trace context is not modeled as propagated; which health checks nothing checks; which alert
rules watch signals that are not emitted or cannot be delivered; which observability policy rules and
requirements are satisfied, violated or impossible to evaluate; and what to model or investigate
next.

It is kept apart from the other engines: availability, redundancy and recovery are the
[reliability engine](reliability-engine.md)'s; throughput and saturation the capacity engine's;
audit logging and data protection the [security engine](security-engine.md)'s (whose data
sensitivity reading the logging analyzer reuses).

Out of scope: live metrics ingestion, log collection or querying, distributed trace collection or
visualization, Prometheus / Grafana or OpenTelemetry collector deployment, alert delivery and
incident management, runtime SLO evaluation and error budgets, real-time dashboards, automated
instrumentation, language-model conclusions, and ArchitectOS's own logs and metrics.

## Supported analyzers and coverage categories

In run order (`GET /observability/analyzers` returns each one's rules, inputs and limits). Every
analyzer skips third parties (`external` components): their internals are not ours to model.

| Analyzer | Category | Finding types |
|---|---|---|
| `criticality` | criticality | `criticality_not_modeled` (one finding listing every component without a declared criticality) |
| `logs` | logging | `logs_absent`, `logs_not_modeled`, `telemetry_not_collected`, `sensitive_data_in_logs` |
| `metrics` | metrics | `metrics_absent`, `metrics_not_modeled`, `telemetry_not_collected` |
| `traces` | tracing | `traces_absent`, `traces_not_modeled`, `telemetry_not_collected`, `propagation_broken`, `propagation_not_modeled` |
| `health-checks` | health_checks | `health_check_absent`, `health_check_not_modeled`, `health_check_unconsumed` |
| `alerts` | alerting | `alerts_absent`, `alerts_not_modeled`, `alert_without_signal`, `alert_delivery_not_modeled` |
| `policy` | policy | `policy_violated`, `policy_not_evaluable` |
| `requirements` | requirement | `requirement_violated`, `requirement_not_evaluable` |

- **Logging, metrics, tracing, health checks, alerting**: a component declared critical that
  declares the capability off (`logs: false`, `metrics: []`, `traces: false`, `health_check: false`,
  `alerts: []`) is a control gap (high); one that does not declare it cannot be evaluated (medium). A
  standard or unclassified component is not reported for what it does not declare.
- **Collection** (`telemetry_not_collected`, per dimension): declared logs, metrics or traces that no
  path of connections declaring that signal in `telemetry` carries to an observability component
  (through collectors or agents).
- **Propagation**: on `request`, `publish` and `consume` flows between two components declaring
  traces, `trace_propagation: false` breaks the trace (`propagation_broken`); not declared cannot be
  evaluated (`propagation_not_modeled`); a component declaring `trace_context: terminate` with traced
  flows in and out ends its callers' traces. Data access, replication and dependencies are not request
  flows.
- **Health checks**: a declared health check that no connection declaring `health_check` targets is
  `health_check_unconsumed` (not evaluable: the model says nothing checks it).
- **Alerting**: an alert rule on a signal the component declares it does not emit
  (`alert_without_signal`); alert rules whose metrics or logs reach no observability component
  declaring an `alert_delivery` other than `none` (`alert_delivery_not_modeled`, naming the backends
  reached or the missing collection).
- **Sensitive data in logs** (potential risk): logs declared on a component whose declared data
  classification or personal data makes it sensitive (the security engine's reading); redaction and
  log content are not modeled.

## Required architecture properties

All optional, with the IR's provenance; nothing is defaulted and nothing is inferred from names,
technologies or providers.

| Element | Property | Values |
|---|---|---|
| Component | `criticality` | `critical`, `standard` |
| Component | `logs`, `structured_logs`, `correlation_ids`, `traces`, `health_check` | boolean |
| Component | `metrics` | metric kinds: `errors`, `latency`, `throughput`, `saturation`, `resources`, `availability` (`[]`: none) |
| Component | `alerts` | the signals alert rules watch: metric kinds, `health`, `logs` (`[]`: none) |
| Component | `trace_context` | `propagate`, `terminate` |
| Component | `trace_sampling_ratio` | 0–1 |
| Component | `owner` | a team or person identifier |
| Observability component | `alert_delivery` | `none`, `email`, `chat`, `paging`, `other` |
| Observability component | `retention_seconds` | whole seconds |
| Connection | `telemetry` | signals carried from its source: `logs`, `metrics`, `traces` |
| Connection | `trace_propagation` | boolean |
| Connection | `health_check` | boolean: the source checks the target's health |

## Coverage aggregation rules

Each component has one **coverage state per dimension** (`logging`, `metrics`, `tracing`,
`health_checks`, `alerting`), from what it declares:

- `modeled`: declared on and complete in the model — logs, metrics and traces collected; a health
  check consumed; alert rules whose signals are emitted and that can be delivered;
- `partial`: declared on but incomplete (no collection path, no consumer, no delivery);
- `absent`: declared off;
- `unknown`: not declared — never read as present, never as absent;
- `unsupported`: a third party (every dimension).

The summary (`ObservabilityResult.summary`) only counts, and every count is reproducible from the
components, findings and checks of the result:

- `scope`: components analyzed, `eligible` (every dimension applies) and `unsupported`, counted
  apart and never folded into another state;
- `coverage` / `criticalCoverage`: per dimension, components (all, or declared critical) in each
  state, zeros included; a component counts once per dimension; dimensions are never combined;
- `collection`: per signal, components declaring it, with and without a modeled collection path;
- `objectives`: `objective_measurable` and `objective_alerted` checks by verdict, and requirement
  checks with no supported condition;
- `findings` by severity, `bases`, `categories`, `checks` by verdict;
- `priorities`: the first ten finding ids in **priority order** — severity, then basis (violation,
  control gap, potential risk, not evaluable), then type and id; the same order sorts `findings`.

There is **no percentage**: a percentage needs a denominator, and a coverage state is not a pass or
a fail. Severity belongs to findings, never to a coverage state.

Status: `completed` when every applicable dimension of every component is declared and every
analyzer ran; `insufficient_input` when components exist but none declares any observability;
`unsupported` when nothing is in scope; otherwise `partial`.

## Finding categories and severity semantics

A finding type fixes its **category** (the table above) and its **basis**, kept apart and enforced by
the domain and the database:

- `control_gap`: a capability declared off where it matters;
- `potential_risk`: the modeled facts could hide or leak something (sensitive data in logs);
- `violation`: a requirement or policy rule contradicted by modeled evidence;
- `not_evaluable`: the model does not say enough to decide.

Severity is a triage order on validation's scale (critical, high, medium, low, info): high for a
critical component's control gap, medium when it cannot be evaluated, lower for standard components;
a policy violation is high; a requirement violation takes the requirement's priority; a verdict that
cannot be reached is one step lower. Findings about one signal carry its `dimension` (part of their
identity); requirement and policy findings carry the `checkKey` they report.

## Evidence and provenance

Every finding names its elements (`nodeIds`, `connectionIds`), the declared facts it was judged on
(`evidence`: element-labelled property values, e.g. `api.configuration.metrics = latency`), what the
architecture would need to declare to decide (`missing`, e.g. `api.configuration.logs`,
`api.collection`, `api.alert_delivery`), its assumptions, and the analyzer and version that produced
it. `certainty` is `modeled`, or `candidate` when a fact it relies on is inferred or proposed (the
IR's provenance). A setting whose name looks like a secret is shown by its path with the value
`"[redacted]"` (one rule shared with the security engine and the architecture diff); the domain
refuses a finding or check that would show one.

## Unknown and unsupported behavior

- An undeclared property is **unknown**: coverage `unknown`, findings `not_evaluable` with what is
  `missing`, checks `not_verifiable`. It is never taken as configured and never as absent.
- An undeclared **criticality** is unknown too: the component is neither critical nor standard, is
  listed once (`criticality_not_modeled`), and makes every check about critical components
  `not_verifiable` until it is declared.
- **Third parties** (`external`) are `unsupported` in every dimension and concerned by no condition.
- A requirement that maps to no supported condition, or whose scope (`user`, `region`) is not modeled,
  gets a check with condition `unsupported` and verdict `not_verifiable`: never a pass.
- An analyzer that fails is contained (reported in `unsupported`, logged by error type only); the
  others' findings stand.

## Requirement and SLO traceability

**Objectives** are in-force `availability`, `reliability` and `performance` requirements with a
structured constraint. Their metric maps to the metric kinds that measure it (`INDICATORS`):

| Objective metric | Metric kinds that measure it |
|---|---|
| `availability` | `availability`, `errors` |
| `latency` | `latency` |
| `requests_per_second`, `orders_per_second` | `throughput` |
| anything else (`durability`, `rpo`, `rto`, sizing) | none: unsupported |

Each objective gets two checks: `requirement.<ref>.objective_measurable` (a concerned component
declares a metric of those kinds and it is collected by a modeled path; not collected is
`not_verifiable`) and `….objective_alerted` (an alert rule on those kinds — or on `health`, for
availability — with a modeled delivery path). A satisfied objective check means a measurable,
alerted indicator is **modeled**; it never means the objective is met, and no attainment, error budget
or burn rate is computed.

**Monitoring requirements** (`operational`, category `monitoring`) are words; a documented keyword
table (`MONITORING`) maps them to fixed conditions, and the words matched are recorded with the
verdict (`mapping`, e.g. `monitoring + 'on-call'`):

| Words | Condition |
|---|---|
| health check, health endpoint, probe | `health_checks_on_critical` |
| alert, page | `alerting_on_critical` |
| log; structured or JSON log | `logs_on_critical`; `structured_logs` |
| correlation, request id | `correlation_ids` |
| metric | `metrics_on_critical` |
| trace; propagate | `traces_on_critical`; `propagation_on_critical` |
| owner, on-call | `ownership` |
| collect, export, centralize, aggregate | `telemetry_collected` |

On what: the components that reference the requirement, else those of its scope (`service`, `api`,
`database`, `queue`, `data`), else (`system`) the components declared critical. `requirementIds` in a
request limits which in-force requirements are evaluated.

## Policy

The project's [architecture policy](../domain/projects.md#architecture-policy) is snapshotted with
each analysis. Each active observability field is one condition, judged by the same code as the
requirements: `require_logs_on_critical`, `required_metric_kinds_on_critical`,
`require_traces_on_critical`, `require_trace_propagation`, `require_health_checks_on_critical`,
`require_alerting_on_critical`, `require_structured_logs`, `require_correlation_ids`,
`require_ownership`, `require_telemetry_collection`, `min_telemetry_retention_seconds`. The verdict
is `violated` when any concerned element is bad, `not_verifiable` when none is bad but any is
unknown, `not_applicable` when nothing is concerned, and `satisfied` only when every concerned element
declares what is asked.

## API contracts

See [docs/api/observability.md](../api/observability.md): run (with `requirementIds`), list, read,
components (filters by criticality, or a dimension's state), findings (filters by severity, type,
category, basis, certainty, dimension) and the analyzer catalog.

## Example analysis

**Instrumentation modeled, runtime observability unverified.** A `checkout` architecture: `web → lb →
api → orders`. `api` is declared critical with structured logs, correlation ids, `errors` and
`latency` metrics, traces, a health check (checked by `lb`), alert rules on `errors` and `health`, and
an owner; `orders` (standard) declares logs and traces; both export telemetry to `obs`, an
observability component with `alert_delivery: paging` and 30 days' retention; `api → orders`
declares `trace_propagation: true`. The policy requires ownership and trace propagation; an active
requirement asks for 99.9 % availability.

```json
POST …/observability-analyses
{"label": "Before launch"}
```

Result (trimmed):

- `status: partial` — `lb`, `obs` and `orders` do not declare every dimension (unknown, not missing);
- `api`: every dimension `modeled`; `orders`: logging and tracing `modeled`, the rest `unknown`;
- `collection`: logs 2/2, metrics 1/1, traces 2/2 collected;
- checks, all `satisfied`: `policy.require_ownership`, `policy.require_trace_propagation`,
  `requirement.req-1.objective_measurable` ("All 1 components concerned declare a collected metric
  measuring the objective."), `requirement.req-1.objective_alerted`;
- findings: none;
- `limitations`: `configuration_only` ("This analysis reads the architecture's declared observability
  configuration. It does not collect or query live telemetry, does not prove that instrumentation is
  emitted, collected, retained or acted on, and does not calculate SLO attainment. Findings require
  engineering review.") and `no_defaults`.

No finding does not mean the service is observable in production: the architecture declares what an
observable service needs, and nothing here verifies that the exporters run, the collector keeps the
data, the alert fires or anyone is paged. Whether checkout is available 99.9 % of the time is not
known — only that an indicator for it is modeled.

## Example: unknown is not configured

The same architecture, with `lb` and `obs` declaring no criticality, `api → orders` not declaring
propagation, and `orders` exporting nothing:

- `status: partial`; `lb` and `obs` every dimension `unknown`; `orders` logging and tracing `partial`;
- checks, all `not_verifiable`: `policy.require_ownership` and both objective checks (missing
  `lb.configuration.criticality`: whether `lb` is critical is not declared), and
  `policy.require_trace_propagation` (missing `api-orders.configuration.trace_propagation`);
- findings, all `not_evaluable`: `policy_not_evaluable`, `requirement_not_evaluable` (metrics and
  alerting), `propagation_not_modeled` (`api-orders`), `criticality_not_modeled` (`lb`, `obs`),
  `telemetry_not_collected` (`orders`: logging and tracing).

Nothing is reported as configured, and nothing as missing: the architecture does not say.

## Adding a deterministic analyzer

1. A class with `meta = AnalyzerMeta(...)`: a new id, version 1, its category, the finding types it
   may produce, its inputs, the IR properties it relies on, its rules in words, the analyzers it
   builds on (registered before it), what it cannot evaluate, its limitations; and
   `analyze(context, progress) -> AnalyzerOutput`.
2. Read facts through the context (`context.facts`, `connection_facts`, `collected(signal)`,
   `backends(signal)`, `health_consumers`, `alert_delivery`, `coverage`) and the shared helpers
   (`support.py`: redacted element-labelled evidence, certainty from provenance). Never infer from
   names or technologies; unknown stays unknown; never read telemetry; never modify the architecture;
   keep the work linear in the IR (compute graph searches once per analysis, in the context's memo).
3. Build findings with `support.finding(self.meta, ...)`; a new finding type needs its category and
   basis in `TYPES` (and a migration for the database check). Name every element, the evidence, what
   is missing, and the dimension.
4. Register it in `engines/observability/registry.py`. A change to what it concludes is a new version.
5. Test the gap, the not-modeled case, the negative case, provenance (candidate), redaction and
   determinism.

## Tests

```
make test-unit          # tests/unit/observability (domain, engine, each analyzer, summary, fixtures, service)
make test-integration   # tests/integration/api/test_observability.py, migrations
make test-security      # sweeps, documentation, traceability (test_traceability_observability_engine.py)
make migrate-check      # migration 0017
make lint typecheck
```

## Known limitations

- Only what is declared: an incompletely modeled architecture yields `unknown` coverage and
  `not_evaluable` findings, not assurance.
- A declared capability is not verified: `logs: true` or `alert_delivery: paging` say what the
  architecture states; exporters, pipelines, retention and on-call rotations are not checked.
- No SLO attainment, error budget, burn rate, sampling adequacy or trace completeness: they need
  runtime measurements.
- Objectives on durability, RPO, RTO and sizing metrics have no indicator mapping (unsupported).
- Monitoring requirements are mapped by keywords: other words (dashboards, runbooks, "pager") leave
  a requirement unsupported until it is rephrased or the table grows.
- Dashboards are not modeled (no IR property); `engines/observability/dashboards.py` is an empty
  placeholder from before this milestone.
- Log content, fields and redaction, metric labels and cardinality are not modeled.
- Measured on the largest architecture (1,000 nodes, 5,000 connections, every policy rule, two
  requirements): 0.50 s to analyze (12,004 findings); storage is proportional to the findings (rows
  inserted in batches of 500).

## Persistence

`observability_analyses` (inputs: request, policy snapshot, requirements read; analyzer set,
fingerprints, summary, checks, unsupported, limitations, error), `observability_components`
(criticality and one coverage column per dimension, for filtering) and `observability_findings`
(priority position, stable id, dimension, a database check that the type fixes the category and
basis). Append-only (triggers); same-project foreign keys to the architecture, the revision and the
analysis. No telemetry and no secret is stored.

## Authorization

Running needs `architecture.analyze` (members and up) on a modifiable project and architecture;
reading needs `architecture.read`. Lookups go project → architecture → analysis; organization,
project and actor come from the path and the session, never the body; unknown body fields are refused
(the policy and any telemetry cannot be sent in a request). The audit entry
(`architecture.observability_analyzed`) carries ids and counts only.

## Determinism

Components and connections in id order, analyzers in registered order, graph searches breadth first
in id order, findings deduplicated by id and ordered by priority, every collection sorted, no clock
or randomness. The context fingerprint covers the revision (content hash), the request, the policy
and the requirements read. Reordering nodes or connections changes nothing.

## Limits and performance

| Limit | Value |
|---|---|
| Architecture | 1,000 nodes, 5,000 connections (IR) |
| Scope, analyzers, requirement ids, assumptions | 200, 50, 200, 50 |
| In-force requirements read | 5,000 |
| Components and findings page | 500 |
| Analyses | 120 per user per hour |

Measured: 1,000 nodes and 5,000 connections, every analyzer, every policy rule and two requirements:
0.50 s (12,004 findings); 500 observability components with 5,000 telemetry connections: 1.55 s.
Collection is one reverse breadth-first search per signal, and the backends reached one per
observability component and signal; every other analyzer is linear in the IR.

## Security of the engine itself

No code, expressions or rules from requests; analyzers are chosen among registered ones; every input
bounded; requirement text is matched against fixed patterns, never executed; the engine imports no
network, telemetry or platform modules (tested); secrets redacted by one shared rule; errors carry
fixed messages. The security sweeps (authentication, tenant isolation, mass assignment, audit,
documentation) cover every endpoint.

## Repository audit

Before any change (phase 0): the seven `engines/observability/*` files (alerts, slo_monitoring,
metrics, service, dashboards, logs, traces), `ai/agents/observability_agent.py` and every `knowledge/*`
file were empty; there was no observability domain, route, table or test. The IR had the
`observability` node kind and `retention_seconds`. Reused: the IR (node kinds, connection kinds,
provenance, `Topology`), the shared element facts, evidence labelling and certainty
(`core/domain/facts.py`), secret redaction (`core/domain/redaction.py`), the check and verdict logic
shared with security (`core/domain/checks.py`), validation's severities, verdicts, requirement scopes
and priority severities, capacity's certainty, the security engine's sensitivity reading, the
project's architecture policy (extended), and the engine pattern of the earlier engines (registry,
orchestrator, port, stored append-only analyses, three-step service, sweeps).

## Final review

Two independent reviews (security, correctness):

- security: nothing found — authorization and tenant isolation, redaction of secrets in responses,
  rows, audit and logs, input validation, error handling, SQL, rate limits and the policy fields;
- correctness, high (fixed with a regression test): the collection search kept one path per node, so
  a node whose telemetry reached two observability components was judged by whichever the search met
  first, and alert delivery through the other was reported as not modeled; every component reached is
  now recorded.

Confirmed sound: verdict logic, finding identity and deduplication, the database checks, pagination,
requirement filtering, criticality gating, determinism and bounded cost.
