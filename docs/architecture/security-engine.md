# Security engine

Deterministic, explainable, **architecture-level** security analysis of an architecture revision:
where the modeled data flows cross trust boundaries, which authentication, authorization,
encryption and secret-management controls the architecture models, what is exposed and what that
reaches, STRIDE threat candidates, and whether the project's security policy and requirements are
met by what the architecture declares.

- It performs **architecture-level analysis** of what the architecture **models**.
- It **does not prove the absence of vulnerabilities**: no finding means only that nothing was
  detected in what is modeled.
- It **does not replace** secure implementation review, penetration testing, dependency scanning or
  operational security controls.
- **Findings require engineering review.** Recommendations are options; nothing is changed
  automatically. There is no score and no compliance claim.

No language model takes part; the same revision, request, policy and requirements always give the
same result and `resultFingerprint`.

Code: `core/domain/security/` (inputs, values, results, request and lifecycle, report, service),
`engines/security/` (context, orchestrator, analyzers, conditions), `persistence/` (migration 0016),
`apps/api/routes/security.py`. API: [docs/api/security.md](../api/security.md). Decision:
[ADR-015](../adr/ADR-015-deterministic-security.md). Frontend:
[security contract](../frontend/security-contract.md).

## Purpose and scope

It helps answer: which security-relevant components and flows exist; where the trust boundaries
are; what exposes sensitive data or services; whether authentication, authorization, encryption and
secret management are modeled; which threats the modeled facts could allow; which security
requirements and policy rules are satisfied, violated or impossible to evaluate; and what to
investigate.

Out of scope: penetration testing, dynamic testing, source-code and dependency (CVE) scanning, cloud
account inventory, compliance certification, SIEM/SOC integration, intrusion detection, automated
remediation or architecture changes, language-model conclusions, and ArchitectOS's own
authentication, sessions and roles (the platform's: [authentication](../security/authentication.md)).

## Supported analyzers and rule categories

In run order (`GET /security/analyzers` returns each one's rules, inputs and limits):

| Analyzer | Category | Finding types |
|---|---|---|
| `trust-boundaries` | trust boundary | `unprotected_boundary_crossing`, `crossing_controls_not_modeled`, `sensitive_data_crosses_boundary`, `trust_level_not_modeled`, `inconsistent_trust_boundary`, `insufficient_flow_semantics` |
| `authentication` | authentication | `missing_authentication`, `authentication_not_modeled`, `unauthenticated_connection`, `inconsistent_authentication` |
| `authorization` | authorization | `missing_authorization`, `authorization_not_modeled` |
| `encryption` | encryption | `unencrypted_data_at_rest`, `unencrypted_data_in_transit`, `encryption_not_modeled` |
| `data-protection` | data protection | `data_classification_not_modeled` |
| `secrets` | secrets | `hardcoded_secret`, `secret_source_not_modeled`, `secret_in_configuration` |
| `exposure` | exposure | `public_management_interface`, `sensitive_component_reachable_from_public`, `exposure_not_modeled` |
| `threat-model` | threat | `threat_candidate` (STRIDE) |
| `policy` | policy | `policy_violated`, `policy_not_evaluable` (and checks) |
| `requirements` | requirement | `requirement_violated`, `requirement_not_evaluable` (and checks) |

Rules in brief:

- **Boundaries** are only what is declared: trust zones (`boundary_type: trust_zone`; a flow crosses
  when the innermost zones of its ends differ, one end in no zone included) and exposure boundaries
  (public against internal or private). A crossing declaring `tls: false` over an unencrypted protocol
  or `authentication: none` is unprotected; one declaring neither is not evaluable; one carrying
  sensitive data is a potential risk even when protected.
- **Authentication** is needed by a component declared public, performing sensitive operations, or
  declaring an authorization model; `none` is a gap, nothing declared is not modeled. Service
  connections declaring `authentication: none` carry no identity. Paths into one component that
  authenticate differently, one not at all, are a potential risk.
- **Authorization** is needed by sensitive operations (high) and sensitive data (medium).
- **Encryption**: sensitive stores declaring `encryption_at_rest: false`; sensitive flows declaring
  `tls: false` (a flow is sensitive as it declares, else as the store whose data it moves declares).
- **Secrets**: `secret_source: hardcoded`; secrets needed with no source; settings preserved in the
  architecture (`configuration.extra`, `metadata`, any depth) named like secrets and with a value,
  reported by name with the value redacted.
- **Exposure**: public management interfaces; sensitive components, sensitive operations and
  management interfaces reached from a public component by a modeled path (the exact path, from the
  nearest entry, one breadth-first search).
- **Threats**: STRIDE candidates from the findings above, by a documented mapping (below).
- **Policy and requirements**: fixed conditions (below).

## Required architecture properties

All optional IR properties, declared by a person or read from a system, never defaulted
([architecture IR](architecture-ir.md)):

| On | Property | Values |
|---|---|---|
| components | `exposure` | `public` (the internet), `internal` (the organization's networks), `private` |
| | `authentication` | `none`, `password`, `api_key`, `token`, `oauth2`, `mtls`, `iam`, `other` |
| | `authorization` | `none`, `rbac`, `abac`, `acl`, `policy`, `other` |
| | `sensitive_operations`, `management_interface`, `personal_data`, `secrets_required`, `secret_rotation`, `audit_logging` | booleans |
| | `data_classification` | `public` < `internal` < `confidential` < `restricted` |
| | `encryption_at_rest` | boolean (data stores, queues, observability) |
| | `secret_source` | `secret_manager`, `environment`, `file`, `configuration`, `hardcoded` |
| boundaries | `boundary_type: trust_zone`, `trust_level` | `untrusted`, `partner`, `internal`, `restricted` |
| connections | `tls`, `authentication`, `data_classification`, `personal_data`; `protocol`, `kind`, direction | |

A component's **coverage** is judged on what matters for it: exposure, data classification,
authentication and whether it needs secrets (a third party: its classification only); encryption at
rest for data stores; authorization once it declares sensitive operations; a secret source once it
declares it needs secrets.

## Finding categories and severity semantics

Every finding's **type** fixes its **category** and its **basis** — the four kinds kept apart:

- `control_gap`: the model states a control is absent or disabled where it matters;
- `potential_risk`: the modeled structure could allow harm; not established that it does;
- `violation`: an explicit requirement or policy rule is contradicted by modeled evidence;
- `not_evaluable`: the model does not say enough to decide.

**Severity** uses validation's scale (critical, high, medium, low, info) as a triage order, not a
risk score: high for gaps on sensitive data or operations and public management interfaces, medium
for gaps elsewhere and unknowns that matter, low for modeling gaps. Policy violations are high;
requirement violations take the requirement's priority; a verdict that cannot be reached is one
step lower. **Certainty** is `modeled` when every fact used was declared by a person or read from a
system, `candidate` when any was inferred or proposed by a language model.

Ids are stable (`sec_` + a hash of type, elements, and threat, requirement, policy rule or check):
the same finding keeps its id across analyses.

## Evidence and provenance

Each finding names its node, connection and trust-zone ids and its evidence: the declared facts
used, labelled `element.configuration.property`, each with its provenance (`false (llm_proposal,
inferred)`), plus derived facts (`api-db.crosses`, `db.path`, `threat.derived_from`). What a
decision would need is in `missing`. A mechanism's name is what the architecture says, never proof
that it is implemented correctly (stated in each finding's assumptions).

**Secrets are never shown.** A value whose name looks like a secret (`password`, `token`, `api_key`,
`private_key`, `secret`, `credential`, …) — a preserved key is tested whole, dots included — is
`[redacted]` in findings, checks, responses and storage; a finding or check that would show one is
refused by the domain. Engine failures are logged by the error's type only, never its message.

## Unknown and unsupported behavior

- An absent or explicitly unknown property is **not modeled**: never taken as present, never as
  absent. It produces a `not_evaluable` finding where it matters and a `missing` entry.
- Sensitivity is established only by a declared classification or personal data; otherwise it is
  unknown, and the flows and stores concerned are reported as not classified.
- A requirement or policy check is `satisfied` only when every concerned element declares what it
  asks; `not_verifiable` when anything deciding it — including whether an element is concerned —
  is not declared. Never a pass on missing evidence.
- Status: `completed`, `partial`, `insufficient_input` (nothing modeled: unknown is not secure),
  `unsupported` (nothing in scope), `failed`. An analyzer that fails is `analyzer_failed` (or
  `invalid_output`); the other analyzers' findings still count.

## Threat-model assumptions

STRIDE (the categories the web app already uses), mapped from findings by a fixed table in
`engines/security/threat_model.py` (published in the analyzer's rules): missing or inconsistent
authentication and unauthenticated connections → spoofing; unprotected crossings → spoofing when
`authentication: none`, tampering and information disclosure when `tls: false`; unencrypted data →
information disclosure (and tampering in transit); missing authorization and public management
interfaces → elevation of privilege; hardcoded secrets → information disclosure and spoofing;
reachable sensitive components → information disclosure (data) or elevation of privilege
(operations, management); sensitive operations without audit logging → repudiation. Findings about
the same elements form one candidate that names them (`threat.derived_from`). A candidate is what
the modeled facts **could** allow — no likelihood, exploitability, impact, CVE or CWE. Denial of
service is not derived: rate limits, quotas and abuse capacity are not modeled.

## Requirements and policy

**Policy** (the project's, snapshotted with each analysis): `require_tls`,
`require_encryption_at_rest`, `require_authentication_on_public`,
`require_authorization_on_sensitive`, `prohibit_public_management_interfaces`,
`approved_secret_sources`, `require_secret_rotation`, `require_audit_logging`,
`require_data_classification` — each one fixed condition.

**Requirements**: in-force `security` requirements, mapped by a documented keyword table
(`engines/security/requirements.py`): encryption + at rest / stored / database → encryption at rest;
encryption + in transit / TLS / HTTPS → encryption in transit; authentication + public / internet →
authentication on public; authorization + sensitive / admin / operations → authorization on
sensitive; admin or management interface + public → no public management interface; secrets +
secret manager / vault / KMS → approved source; secrets + rotation → rotation; audit → audit logging;
classification → data classification. Words like "customer", "personal" or "sensitive" narrow a
condition to what is declared sensitive. The mapping used is recorded with each verdict; anything
unmatched (MFA, SSO, key lengths…) or scoped to users or regions is `unsupported`, never passed.

## API contracts

See [docs/api/security.md](../api/security.md): run, list, read, components, findings (filters by
severity, type, category, basis, certainty, threat) and the analyzer catalog.

## Example analysis

An internet-facing `api` (OAuth 2, RBAC, secrets from a secret manager) in an `untrusted` trust zone
reads a `restricted` database holding personal data in an `internal` zone over TLS; the database
declares `encryption_at_rest: false`; the policy requires encryption at rest.

```json
POST …/security-analyses
{"label": "Before launch"}
```

Result (trimmed): `status: completed` (both components model what matters for them); summary bases
`{control_gap: 1, potential_risk: 4, violation: 1, not_evaluable: 4}`; check
`policy.require_encryption_at_rest: violated` ("db does not declare encryption at rest."). Findings,
most severe first:

| Type | Basis | Severity | Elements |
|---|---|---|---|
| `policy_violated` | violation | high | db |
| `threat_candidate` (information_disclosure) | potential risk | high | db |
| `unencrypted_data_at_rest` | control gap | high | db |
| `sensitive_component_reachable_from_public` | potential risk | medium | api → db (`api-db`) |
| `authorization_not_modeled` | not evaluable | low | db |
| `crossing_controls_not_modeled` | not evaluable | low | `web-api` (its authentication) |
| `sensitive_data_crosses_boundary` | potential risk | low | `api-db` (protected: tls, password) |
| … | | | |

```json
{
  "id": "sec_0b1bff38c5ddab3b", "type": "unencrypted_data_at_rest", "category": "encryption",
  "basis": "control_gap", "severity": "high", "certainty": "modeled",
  "title": "db stores sensitive data unencrypted",
  "nodeIds": ["db"],
  "evidence": [
    {"label": "db.configuration.encryption_at_rest", "value": "false"},
    {"label": "db.configuration.data_classification", "value": "restricted"},
    {"label": "db.configuration.personal_data", "value": "true"}
  ],
  "assumptions": ["No algorithm, key length, key management or protocol version is assumed or checked."],
  "recommendation": "Review whether db should encrypt its data at rest."
}
```

## Example: cannot evaluate, not secure

The same shape with nothing security-related declared (`web → api → db`, plain `http` and
`postgresql`), under the same policy:

- `status: insufficient_input` — no component models any security property;
- every component `coverage: not_modeled`, e.g. db missing `configuration.authentication`,
  `data_classification`, `encryption_at_rest`, `exposure`, `secrets_required`;
- check `policy.require_encryption_at_rest: not_verifiable`, missing
  `db.configuration.encryption_at_rest` ("db does not declare enough to decide (missing evidence is
  never success).");
- findings, all `not_evaluable`: `policy_not_evaluable` (db), `data_classification_not_modeled` (db),
  `exposure_not_modeled` (api, called directly by a client).

Nothing is reported as secure, and nothing as insecure: the architecture does not say.

## Adding a deterministic analyzer

1. A class with `meta = AnalyzerMeta(...)`: a new id, version 1, its category, the finding types it
   may produce, its inputs, the IR properties it relies on, its rules in words, the analyzers it
   builds on (registered before it), what it cannot evaluate, its limitations; and
   `analyze(context, progress) -> AnalyzerOutput`.
2. Read facts through the context (`context.facts`, `connection_facts`, `boundary_facts`,
   `trust_zones`) and the shared readings (`support.py`: three-valued transport protection and
   authentication, redacted element-labelled evidence, certainty from provenance). Never infer from
   names or technologies; unknown stays unknown; never modify the architecture.
3. Build findings with `support.finding(self.meta, ...)`; a new finding type needs its category and
   basis in `TYPES` (and the migration's check). Name every element, the evidence, what is missing.
4. Register it in `engines/security/registry.py`. A change to what it concludes is a new version.
5. Test the gap, the not-modeled case, the negative case, provenance (candidate), redaction and
   determinism.

## Tests

```
make test-unit          # tests/unit/security (domain, engine, each analyzer, fixtures, service)
make test-integration   # tests/integration/api/test_security.py, migrations
make test-security      # sweeps, documentation, traceability (test_traceability_security_engine.py)
make migrate-check      # migration 0016
```

## Known limitations

- Only what is modeled: undeclared boundaries, controls and reachability are not inferred (by
  design), so an incompletely modeled architecture yields `not_evaluable` findings, not assurance.
- A declared control is not verified: `oauth2`, `rbac` or `tls: true` say what the architecture
  states, not that it is implemented or configured well; no algorithms, key lengths, protocol
  versions or policies are checked.
- Threat candidates are architecture-level (STRIDE), without likelihood or impact; denial of
  service, supply chain and runtime threats are not derived.
- Requirement mapping is by keywords: words outside the table leave a requirement unsupported;
  negated phrasing is read by its keywords (it asks for the control, as security requirements do).
- Stale data is not judged (the analysis reads no clock); provenance only marks inferred or proposed
  values.
- The largest all-gaps architecture (1,000 nodes, 5,000 connections, 32,769 findings) takes 9.4 s
  to analyze and store (1.3 s analysis); bounded by the IR's size and the rate limit.

## Persistence

`security_analyses` (inputs: request, policy snapshot, requirements read; analyzer set,
fingerprints, summary, trust zones, checks, unsupported, limitations, error), `security_components`
(coverage) and `security_findings` (canonical position, stable id, a database check that the type
fixes the category and basis, STRIDE only on threat candidates). Append-only (triggers); same-project
foreign keys to the architecture, the revision and the analysis. No secret is stored.

## Authorization

Running needs `architecture.analyze` (members and up) on a modifiable project and architecture;
reading needs `architecture.read`. Lookups go project → architecture → analysis; organization,
project and actor come from the path and the session, never the body; unknown body fields are
refused (the policy cannot be sent in a request). The audit entry (`architecture.security_analyzed`)
carries ids and counts only.

## Determinism

Components and connections in id order, analyzers in registered order, one breadth-first search in
id order, findings deduplicated by id and ordered by severity, type and id, every collection sorted,
no clock or randomness. The context fingerprint covers the revision (content hash), the request, the
policy and the requirements read. Reordering nodes or connections changes nothing.

## Limits and performance

| Limit | Value |
|---|---|
| Architecture | 1,000 nodes, 5,000 connections (IR) |
| Scope, analyzers, assumptions | 200, 50, 50 |
| Components and findings page | 500 |
| Analyses | 120 per user per hour |

Measured: 1,000 nodes and 5,000 crossing connections, one trust-boundary analyzer: 0.31 s; the full
registry on the largest all-gaps shape: 1.3 s (32,769 findings); through the API with storage:
9.4 s, 88 SQL statements; reads 0.01–0.04 s at 9–10 statements whatever the analysis size. Every
analyzer is linear in the IR's size (crossings and reachability computed once per analysis).

Risks: analyses are synchronous on a worker thread shared with the other engines; the worst case is
dominated by storing tens of thousands of findings.

## Security of the engine itself

No code, expressions or rules from requests; analyzers are chosen among registered ones; every input
bounded; requirement text is matched against fixed patterns, never executed; secrets redacted by one
shared rule (also the architecture diff's); errors carry fixed messages. The security sweeps
(authentication, tenant isolation, mass assignment, audit) cover every endpoint.

## Repository audit

Before any change (phase 0): the nine `engines/security/*` files, `ai/agents/security_agent.py`,
`engines/validation/rules/security.py`, `core/domain/components/*` and every `knowledge/*` file were
empty; there was no security domain, route, table or test. Reused: the IR (trust-zone boundaries,
`tls`, protocols, direction, provenance, `Topology`), the shared element facts (moved from
reliability to `core/domain/facts.py`), the IR diff's secret redaction (made shared), validation's
severities, verdicts, requirement scopes and priority severities, capacity's certainty, the
encrypted-protocol list (moved to the IR, shared with validation), the project's architecture policy
(extended), and the engine pattern of validation, capacity, cost and reliability (registry,
orchestrator, port, stored append-only analyses, three-step service, sweeps).

## Final review

Two independent reviews (security, correctness), every finding fixed with a regression test:

- security, critical (pre-existing in the IR diff): a preserved key containing a dot
  (`password.hash`) was judged by its last segment, so its value was shown in architecture diffs and
  missed by the secrets analyzer; keys are now tested whole and ambiguity redacts;
- correctness, high: one requirement checked as two conditions on the same element produced one
  finding id, dropping a violation; requirement and policy findings now carry their `check_key`;
- correctness, medium: "unencrypted" did not count as speaking of encryption (a pii requirement went
  unchecked);
- correctness, low: a docstring on external components.

Confirmed sound: tenant isolation and IDOR, authorization, mass assignment, SQL and regex safety, log
hygiene, CPU bounds of every analyzer, verdict logic, reachability, trust-zone nesting, evidence
parsing, determinism, API wording.
