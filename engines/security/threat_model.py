"""Threat modeling: STRIDE threat candidates grounded in the other analyzers' findings and the
architecture's declared facts. Deterministic rules only — no language model, no likelihood,
exploitability, business impact or score, and no CVE or vulnerability identifier.

**Taxonomy.** STRIDE (spoofing, tampering, repudiation, information disclosure, denial of service,
elevation of privilege), the categories the web app's contract already uses. The mapping is fixed
and documented here (``STRIDE``): each finding type that can enable a threat maps to the categories
it could enable. A few depend on the finding's evidence:

- an unprotected boundary crossing enables **spoofing** when it declares ``authentication: none``,
  and **tampering** and **information disclosure** when it declares ``tls: false``;
- a sensitive component reachable from a public entry enables **information disclosure** when it
  holds sensitive data and **elevation of privilege** when it performs sensitive operations or
  exposes a management interface.

**Candidates.** Source findings are grouped by STRIDE category and the elements they concern; each
candidate lists the findings it derives from (``threat.derived_from``), the first of their evidence
(all of it stays on those findings), the trust zones of its elements, what
it assumes and what is missing, and mitigations for human review. One rule stands on declared facts
directly: a component performing sensitive operations that declares ``audit_logging: false``
(**repudiation**; with audit logging not declared, a candidate with the property missing).

A candidate says what a modeled gap could allow, never that an attack is possible or likely.
Denial of service is not derived: the architecture schema does not model rate limits, quotas or
capacity against abuse.
"""

from collections.abc import Iterable
from typing import Any

from core.domain.capacity.results import Certainty
from core.domain.engine_results import Evidence, Unsupported
from core.domain.security.results import FindingCategory, FindingType, SecurityFinding, StrideCategory
from core.domain.validation.results import Severity

from .context import SecurityContext
from .engine import ARCHITECTURE, AnalyzerMeta, AnalyzerOutput, Progress, names
from .support import certainty, evidence, finding

T = FindingType
S = StrideCategory

# The documented mapping: finding type -> the STRIDE categories it could enable (``categories``
# refines the two that depend on evidence).
STRIDE: dict[FindingType, tuple[StrideCategory, ...]] = {
    T.MISSING_AUTHENTICATION: (S.SPOOFING,),
    T.UNAUTHENTICATED_CONNECTION: (S.SPOOFING,),
    T.INCONSISTENT_AUTHENTICATION: (S.SPOOFING,),
    T.UNPROTECTED_BOUNDARY_CROSSING: (S.SPOOFING, S.TAMPERING, S.INFORMATION_DISCLOSURE),
    T.SENSITIVE_DATA_CROSSES_BOUNDARY: (S.INFORMATION_DISCLOSURE,),
    T.MISSING_AUTHORIZATION: (S.ELEVATION_OF_PRIVILEGE,),
    T.UNENCRYPTED_DATA_AT_REST: (S.INFORMATION_DISCLOSURE,),
    T.UNENCRYPTED_DATA_IN_TRANSIT: (S.INFORMATION_DISCLOSURE, S.TAMPERING),
    T.HARDCODED_SECRET: (S.INFORMATION_DISCLOSURE, S.SPOOFING),
    T.SECRET_IN_CONFIGURATION: (S.INFORMATION_DISCLOSURE,),
    T.PUBLIC_MANAGEMENT_INTERFACE: (S.ELEVATION_OF_PRIVILEGE,),
    T.SENSITIVE_COMPONENT_REACHABLE_FROM_PUBLIC: (S.INFORMATION_DISCLOSURE, S.ELEVATION_OF_PRIVILEGE),
}
SOURCE_ANALYZERS = frozenset(
    {"trust-boundaries", "authentication", "authorization", "encryption", "secrets", "exposure"}
)
WHAT: dict[StrideCategory, str] = {
    S.SPOOFING: "a caller or service could claim an identity it does not have",
    S.TAMPERING: "data could be modified on its way or where it is kept",
    S.REPUDIATION: "sensitive actions could not be traced to who performed them",
    S.INFORMATION_DISCLOSURE: "data could be read by someone not meant to",
    S.DENIAL_OF_SERVICE: "the service could be made unavailable",
    S.ELEVATION_OF_PRIVILEGE: "someone could do more than they are allowed to",
}
MITIGATIONS: dict[StrideCategory, str] = {
    S.SPOOFING: "Options for review: authenticate every caller and service (e.g. OAuth 2 / OIDC for "
    "users, mutual TLS or workload identity between services), consistently on every path.",
    S.TAMPERING: "Options for review: encrypt and integrity-protect data in transit (TLS), restrict "
    "who can write, and verify what is received.",
    S.REPUDIATION: "Options for review: record security-relevant actions in an audit log that the "
    "actors cannot alter.",
    S.INFORMATION_DISCLOSURE: "Options for review: encrypt sensitive data at rest and in transit, keep "
    "secrets in a secret manager, and limit what is reachable and who may read it.",
    S.DENIAL_OF_SERVICE: "Options for review: rate limits, quotas and capacity against abuse.",
    S.ELEVATION_OF_PRIVILEGE: "Options for review: authorize every sensitive operation per caller, keep "
    "management interfaces off public networks, and grant least privilege.",
}
STRIDE_ASSUMPTION = (
    "A threat candidate is what the modeled facts could allow (STRIDE), not a demonstrated or likely "
    "attack: no likelihood, exploitability or impact is estimated."
)
SENSITIVE_DATA = (
    ("personal_data", "true"),
    ("data_classification", "confidential"),
    ("data_classification", "restricted"),
)

SHOWN = 5  # evidence items copied from the sources (all of it is on the source findings)

type GroupKey = tuple[StrideCategory, tuple[str, ...], tuple[str, ...]]


def _declares(source: SecurityFinding, suffix: str, value: str) -> bool:
    """Whether the finding's evidence shows ``suffix`` declared as ``value`` (provenance aside)."""
    return any(e.label.endswith(suffix) and e.value.split(" ", 1)[0] == value for e in source.evidence)


def categories(source: SecurityFinding) -> tuple[StrideCategory, ...]:
    """The STRIDE categories a finding could enable, by the documented mapping and its evidence."""
    if source.type is T.UNPROTECTED_BOUNDARY_CROSSING:
        spoofing = (S.SPOOFING,) if _declares(source, ".configuration.authentication", "none") else ()
        exposed = _declares(source, ".configuration.tls", "false")
        return spoofing + ((S.TAMPERING, S.INFORMATION_DISCLOSURE) if exposed else ())
    if source.type is T.SENSITIVE_COMPONENT_REACHABLE_FROM_PUBLIC:
        target = source.evidence[0].label.split(".reachable_from", 1)[0] if source.evidence else ""
        data = any(_declares(source, f"{target}.configuration.{n}", v) for n, v in SENSITIVE_DATA)
        privilege = any(
            _declares(source, f"{target}.configuration.{name}", "true")
            for name in ("sensitive_operations", "management_interface")
        )
        disclosure = (S.INFORMATION_DISCLOSURE,) if data else ()
        return disclosure + ((S.ELEVATION_OF_PRIVILEGE,) if privilege else ())
    return STRIDE.get(source.type, ())


def _unique(items: Iterable[Evidence]) -> tuple[Evidence, ...]:
    return tuple(dict.fromkeys(items))


class ThreatModel:
    meta = AnalyzerMeta(
        id="threat-model",
        version=1,
        name="Threat model (STRIDE)",
        description="STRIDE threat candidates grounded in the other analyzers' findings and declared "
        "audit logging, with evidence, trust zones, assumptions and mitigations for review.",
        category=FindingCategory.THREAT,
        finding_types=(T.THREAT_CANDIDATE,),
        inputs=("components", "boundaries", "findings"),
        properties=("sensitive_operations", "audit_logging"),
        rules=(
            *(f"{t.value} → {', '.join(s.value for s in cats)}" for t, cats in STRIDE.items()),
            "unprotected_boundary_crossing → spoofing when authentication is none; tampering and "
            "information_disclosure when tls is false.",
            "sensitive_component_reachable_from_public → information_disclosure for sensitive data; "
            "elevation_of_privilege for sensitive operations or a management interface.",
            "sensitive_operations with audit_logging false (or not declared) → repudiation.",
        ),
        unsupported=(
            "Denial of service (rate limits, quotas and abuse capacity are not modeled).",
            "Likelihood, exploitability, business impact, CVEs and vulnerability identifiers.",
        ),
        limitations=(STRIDE_ASSUMPTION,),
    )

    def analyze(self, context: SecurityContext, progress: Progress) -> AnalyzerOutput:
        selected = context.request.analyzers
        if selected is not None and not SOURCE_ANALYZERS & set(selected):
            return AnalyzerOutput(
                unsupported=(
                    Unsupported(
                        ARCHITECTURE,
                        "no_source_findings",
                        "The threat model builds on the other analyzers' findings; none of them was "
                        "selected, so no threat candidate can be derived.",
                    ),
                )
            )
        groups: dict[GroupKey, list[SecurityFinding]] = {}
        for source in progress.findings:
            for category in categories(source):
                groups.setdefault((category, source.node_ids, source.connection_ids), []).append(source)
        findings = [self._candidate(context, key, groups[key]) for key in sorted(groups)]
        findings += self._repudiation(context)
        return AnalyzerOutput(tuple(findings))

    def _zones(self, context: SecurityContext, node_ids: Iterable[str]) -> set[str]:
        return {z for n in node_ids for z in context.zones_of.get(n, ())}

    def _candidate(
        self, context: SecurityContext, key: GroupKey, sources: list[SecurityFinding]
    ) -> SecurityFinding:
        category, node_ids, connection_ids = key
        sources = sorted(sources, key=lambda f: f.id)
        elements = [*node_ids, *connection_ids]
        derived = ", ".join(f.id for f in sources)
        zones = self._zones(context, node_ids) | {b for f in sources for b in f.boundary_ids}
        fields: dict[str, Any] = {
            "node_ids": node_ids,
            "connection_ids": connection_ids,
            "boundary_ids": tuple(sorted(zones)),
            # the source findings carry their full evidence; a candidate names them and shows the first
            "evidence": (
                Evidence("threat.derived_from", derived),
                *_unique(e for f in sources for e in f.evidence)[:SHOWN],
            ),
            "assumptions": (STRIDE_ASSUMPTION, *sorted({a for f in sources for a in f.assumptions})),
            "missing": tuple(sorted({m for f in sources for m in f.missing})),
        }
        worst = min((f.severity for f in sources), key=list(Severity).index)
        sure = all(f.certainty is Certainty.MODELED for f in sources)
        label = category.value.replace("_", " ").capitalize()
        more = f"; and {len(sources) - 3} more" if len(sources) > 3 else ""
        return finding(
            self.meta,
            T.THREAT_CANDIDATE,
            worst,
            Certainty.MODELED if sure else Certainty.CANDIDATE,
            title=f"{label}: {WHAT[category]} ({names(elements, 5)})",
            explanation=f"By the modeled facts, {WHAT[category]}: "
            + "; ".join(f.title for f in sources[:3])
            + more
            + ". This is a candidate for review, not a demonstrated attack.",
            recommendation=MITIGATIONS[category],
            threat=category,
            **fields,
        )

    def _repudiation(self, context: SecurityContext) -> list[SecurityFinding]:
        findings = []
        props = ("sensitive_operations", "audit_logging")
        for node in context.components:
            facts = context.facts[node.id]
            if facts.known("sensitive_operations") is not True:
                continue
            audit = facts.known("audit_logging")
            if audit is True:
                continue
            declared = audit is False
            said = "declares audit_logging false" if declared else "does not declare audit logging"
            findings.append(
                finding(
                    self.meta,
                    T.THREAT_CANDIDATE,
                    Severity.MEDIUM if declared else Severity.LOW,
                    certainty((facts, props)) if declared else Certainty.CANDIDATE,
                    title=f"Repudiation: {WHAT[S.REPUDIATION]} ({node.id})",
                    explanation=f"{node.id} performs sensitive operations and {said}: who performed an "
                    "operation could be disputed. This is a candidate for review, not a demonstrated attack.",
                    recommendation=MITIGATIONS[S.REPUDIATION],
                    threat=S.REPUDIATION,
                    node_ids=(node.id,),
                    boundary_ids=tuple(sorted(self._zones(context, [node.id]))),
                    evidence=evidence(facts, props),
                    assumptions=(STRIDE_ASSUMPTION,),
                    missing=() if declared else (f"{node.id}.configuration.audit_logging",),
                )
            )
        return findings
