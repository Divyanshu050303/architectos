"""Trust-boundary analysis: where the modeled data flows cross a trust boundary, and whether the
architecture models how those crossings are protected.

A **boundary** is only what the architecture declares, never inferred from a name:

- a **trust zone** is a boundary with ``boundary_type: trust_zone`` (its ``trust_level`` says how far
  what runs inside it is trusted; None when not modeled). A connection crosses a trust boundary when
  the innermost trust zones of its two ends differ, including when one end is in a zone and the
  other is in none;
- an **exposure boundary** lies between a component declared ``public`` and one declared
  ``internal`` or ``private``.

For each crossing that carries traffic (not a ``dependency``, whose content is not described):

- ``unprotected_boundary_crossing`` (control gap): the connection declares ``tls: false`` over a
  protocol that is not encrypted by definition, or ``authentication: none``;
- ``crossing_controls_not_modeled`` (not evaluable): transport protection or authentication is not
  modeled — never taken as present, never taken as absent;
- ``sensitive_data_crosses_boundary`` (potential risk): the connection or one of its ends is declared
  sensitive (``confidential``/``restricted`` or personal data), worth review even when protected.

Also: trust zones without a ``trust_level``, and elements in no trust zone when zones are declared
(``trust_level_not_modeled``); a ``trust_level`` on a boundary that is not a trust zone
(``inconsistent_trust_boundary``); crossings whose meaning is too thin to judge — a ``dependency``,
or a bidirectional flow whose authentication describes only one direction
(``insufficient_flow_semantics``). Nothing is said about external components being hostile or
internal ones being trustworthy: only about what is declared.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.domain.capacity.results import Certainty
from core.domain.engine_results import Evidence
from core.domain.facts import ElementFacts
from core.domain.security.results import FindingCategory, FindingType, SecurityFinding
from core.domain.validation.results import Severity

from .context import SecurityContext
from .engine import AnalyzerMeta, AnalyzerOutput, Progress, names
from .support import (
    MECHANISM_NOT_VERIFIED,
    authenticated,
    certainty,
    evidence,
    protocol_evidence,
    transport_protected,
)

T = FindingType
OUTSIDE = "outside every trust zone"
CLASSIFICATION = ("data_classification", "personal_data")


@dataclass(frozen=True, slots=True)
class Crossing:
    """How a connection crosses a declared boundary."""

    connection_id: str
    source_zone: str | None  # innermost trust zone of each end (None: in no zone)
    target_zone: str | None
    zoned: bool  # it crosses a trust zone's edge
    exposure: tuple[str, str] | None  # (source, target) exposures when it crosses an exposure boundary

    @property
    def boundary_ids(self) -> tuple[str, ...]:
        if not self.zoned:
            return ()
        return tuple(z for z in (self.source_zone, self.target_zone) if z is not None)

    def describe(self, context: SecurityContext) -> str:
        parts = []
        if self.zoned:
            source, target = _zone(context, self.source_zone), _zone(context, self.target_zone)
            parts.append(f"trust zones {source} → {target}")
        if self.exposure is not None:
            parts.append(f"exposure {self.exposure[0]} → {self.exposure[1]}")
        return "; ".join(parts)


def _zone(context: SecurityContext, zone: str | None) -> str:
    if zone is None:
        return OUTSIDE
    level = context.boundary_facts[zone].known("trust_level")
    return f"{zone} ({level if isinstance(level, str) else 'trust level not modeled'})"


def _crossing(context: SecurityContext, connection: Connection) -> Crossing | None:
    source, target = connection.source_id, connection.target_id
    source_zones, target_zones = context.zones_of.get(source, ()), context.zones_of.get(target, ())
    source_zone = source_zones[0] if source_zones else None
    target_zone = target_zones[0] if target_zones else None
    zoned = bool(context.trust_zones) and source_zone != target_zone
    exposures = (context.facts[source].known("exposure"), context.facts[target].known("exposure"))
    inner = {"internal", "private"}
    crosses_exposure = (exposures[0] == "public" and exposures[1] in inner) or (
        exposures[1] == "public" and exposures[0] in inner
    )
    if not (zoned or crosses_exposure):
        return None
    exposure = (str(exposures[0]), str(exposures[1])) if crosses_exposure else None
    return Crossing(connection.id, source_zone, target_zone, zoned, exposure)


def crossings(context: SecurityContext) -> Mapping[str, Crossing]:
    """Every connection of the revision that crosses a declared boundary, by connection id (computed
    once per analysis; other analyzers read it)."""
    key = ("trust-boundaries", "crossings")
    if key not in context.memo:
        found = {}
        for connection in context.ir.connections:
            crossing = _crossing(context, connection)
            if crossing is not None:
                found[connection.id] = crossing
        context.memo[key] = found
    result: Mapping[str, Crossing] = context.memo[key]
    return result


class TrustBoundaries:
    meta = AnalyzerMeta(
        id="trust-boundaries",
        version=1,
        name="Trust boundaries",
        description="Declared trust zones and exposure boundaries, the flows crossing them, and whether "
        "their protection is modeled.",
        category=FindingCategory.TRUST_BOUNDARY,
        finding_types=(
            T.UNPROTECTED_BOUNDARY_CROSSING,
            T.CROSSING_CONTROLS_NOT_MODELED,
            T.SENSITIVE_DATA_CROSSES_BOUNDARY,
            T.TRUST_LEVEL_NOT_MODELED,
            T.INCONSISTENT_TRUST_BOUNDARY,
            T.INSUFFICIENT_FLOW_SEMANTICS,
        ),
        inputs=("components", "connections", "boundaries"),
        properties=(
            "boundary_type",
            "trust_level",
            "exposure",
            "tls",
            "authentication",
            "data_classification",
            "personal_data",
        ),
        rules=(
            "A connection crosses a trust boundary when the innermost declared trust zones of its ends "
            "differ, or an exposure boundary when one end is declared public and the other internal or "
            "private.",
            "A crossing that declares tls false over an unencrypted protocol, or authentication none, is "
            "an unprotected crossing.",
            "A crossing whose transport protection or authentication is not declared cannot be evaluated.",
            "A crossing carrying data declared confidential, restricted or personal is a potential risk "
            "worth review.",
            "A trust zone without a trust level, an element in no zone when zones exist, and a trust "
            "level on a boundary that is not a trust zone are reported.",
        ),
        unsupported=(
            "Boundaries that are not declared (no inference from names, networks or providers).",
            "What a dependency connection carries.",
        ),
        limitations=(MECHANISM_NOT_VERIFIED,),
    )

    def analyze(self, context: SecurityContext, progress: Progress) -> AnalyzerOutput:
        findings = [*self._zones(context), *self._inconsistent(context)]
        found = crossings(context)
        for connection in context.connections:
            crossing = found.get(connection.id)
            if crossing is not None:
                findings += self._crossing(context, connection, crossing)
        return AnalyzerOutput(tuple(findings))

    def _finding(
        self, type_: FindingType, severity: Severity, certainty_: Certainty, **fields: Any
    ) -> SecurityFinding:
        return SecurityFinding(
            type=type_,
            severity=severity,
            certainty=certainty_,
            analyzer_id=self.meta.id,
            analyzer_version=self.meta.version,
            **fields,
        )

    def _zones(self, context: SecurityContext) -> list[SecurityFinding]:
        if not context.trust_zones:
            return []
        findings = []
        for zone in context.trust_zones:
            if zone.trust_level is not None:
                continue
            facts = context.boundary_facts[zone.boundary_id]
            findings.append(
                self._finding(
                    T.TRUST_LEVEL_NOT_MODELED,
                    Severity.LOW,
                    certainty((facts, ["boundary_type"])),
                    title=f"The trust zone {zone.boundary_id} has no trust level",
                    explanation=f"{zone.boundary_id} is declared a trust zone, but how far what runs inside "
                    "it is trusted is not modeled, so flows into and out of it cannot be fully judged.",
                    recommendation=f"State the trust_level of {zone.boundary_id} (untrusted, partner, "
                    "internal or restricted).",
                    boundary_ids=(zone.boundary_id,),
                    evidence=evidence(facts, ["boundary_type"]),
                    missing=(f"{zone.boundary_id}.configuration.trust_level",),
                )
            )
        outside = sorted(node_id for node_id, zones in context.zones_of.items() if not zones)
        if outside:
            findings.append(
                self._finding(
                    T.TRUST_LEVEL_NOT_MODELED,
                    Severity.LOW,
                    Certainty.MODELED,
                    title=f"{names(outside)} {'is' if len(outside) == 1 else 'are'} in no trust zone",
                    explanation="Trust zones are declared, but these elements are in none of them: how far "
                    "they are trusted is not modeled. It is not assumed that they are hostile, nor that "
                    "they are trustworthy.",
                    recommendation="Place each element in the trust zone it belongs to, or declare a zone "
                    "for what lies outside the system (e.g. untrusted clients).",
                    node_ids=tuple(outside),
                    evidence=(
                        Evidence("trust_zones", ", ".join(z.boundary_id for z in context.trust_zones)),
                    ),
                )
            )
        return findings

    def _inconsistent(self, context: SecurityContext) -> list[SecurityFinding]:
        findings = []
        for boundary_id, facts in context.boundary_facts.items():
            if facts.trust_zone or facts.known("trust_level") is None:
                continue
            findings.append(
                self._finding(
                    T.INCONSISTENT_TRUST_BOUNDARY,
                    Severity.LOW,
                    certainty((facts, ["boundary_type", "trust_level"])),
                    title=f"{boundary_id} has a trust level but is not a trust zone",
                    explanation=f"{boundary_id} states a trust_level without boundary_type trust_zone, so it "
                    "is not treated as a trust boundary: whether it is meant to be one is unclear.",
                    recommendation=f"Declare {boundary_id} a trust zone, or remove its trust level.",
                    boundary_ids=(boundary_id,),
                    evidence=evidence(facts, ["boundary_type", "trust_level"]),
                    missing=(f"{boundary_id}.configuration.boundary_type",),
                )
            )
        return findings

    def _crossing(
        self, context: SecurityContext, connection: Connection, crossing: Crossing
    ) -> list[SecurityFinding]:
        link = context.connection_facts[connection.id]
        ends = (context.facts[connection.source_id], context.facts[connection.target_id])
        where = crossing.describe(context)
        label = f"{connection.id} ({connection.source_id} → {connection.target_id})"
        common: dict[str, Any] = {
            "node_ids": (connection.source_id, connection.target_id),
            "connection_ids": (connection.id,),
            "boundary_ids": crossing.boundary_ids,
        }
        placed: list[tuple[ElementFacts, list[str]]] = (
            [(end, ["exposure"]) for end in ends] if crossing.exposure is not None else []
        )
        crosses = (Evidence(f"{connection.id}.crosses", where),)
        if connection.kind is ConnectionKind.DEPENDENCY:
            return [
                self._finding(
                    T.INSUFFICIENT_FLOW_SEMANTICS,
                    Severity.LOW,
                    certainty(*placed),
                    title=f"What crosses the boundary over {label} is not described",
                    explanation=f"{connection.id} crosses {where}, but it is a dependency: what flows "
                    "across (configuration, credentials, data) is not described, so its protection cannot "
                    "be judged.",
                    recommendation=f"Model what {connection.source_id} exchanges with {connection.target_id} "
                    "as a request or data access, with its protocol and controls.",
                    evidence=crosses,
                    **common,
                )
            ]
        findings = []
        protected, auth = transport_protected(connection, link), authenticated(link)
        sensitive = link.sensitive is True or any(end.sensitive is True for end in ends)
        controls: tuple[ElementFacts, list[str]] = (link, ["tls", "authentication"])
        shown = crosses + protocol_evidence(connection) + evidence(link, ["tls", "authentication"])
        missing = tuple(
            f"{connection.id}.configuration.{name}"
            for name, known in (("tls", protected), ("authentication", auth))
            if known is None
        )
        if protected is False or auth is False:
            gaps = [
                gap
                for gap, present in (
                    ("is not encrypted in transit", protected is False),
                    ("does not authenticate", auth is False),
                )
                if present
            ]
            findings.append(
                self._finding(
                    T.UNPROTECTED_BOUNDARY_CROSSING,
                    Severity.HIGH if sensitive else Severity.MEDIUM,
                    certainty(controls, *placed),
                    title=f"{label} crosses a trust boundary and {' and '.join(gaps)}",
                    explanation=f"{connection.id} crosses {where}. The architecture declares that it "
                    f"{' and '.join(gaps)}: what crosses the boundary is not protected by those controls.",
                    recommendation=f"Review whether {connection.id} should be encrypted in transit and "
                    "authenticate its source, or whether the flow should cross the boundary at all.",
                    evidence=shown,
                    missing=missing,
                    assumptions=(MECHANISM_NOT_VERIFIED,),
                    **common,
                )
            )
        elif missing:
            unstated = " and ".join(m.rsplit(".", 1)[1] for m in missing)
            findings.append(
                self._finding(
                    T.CROSSING_CONTROLS_NOT_MODELED,
                    Severity.MEDIUM if sensitive else Severity.LOW,
                    certainty(controls, *placed),
                    title=f"How {label} is protected across a trust boundary is not modeled",
                    explanation=f"{connection.id} crosses {where}, but its {unstated} "
                    f"{'is' if len(missing) == 1 else 'are'} not declared: the crossing can be neither "
                    "shown protected nor shown unprotected.",
                    recommendation=f"State tls and authentication for {connection.id}.",
                    evidence=shown,
                    missing=missing,
                    **common,
                )
            )
        if connection.bidirectional and auth is True:
            findings.append(
                self._finding(
                    T.INSUFFICIENT_FLOW_SEMANTICS,
                    Severity.LOW,
                    certainty(controls, *placed),
                    title=f"Only one direction of {label} is authenticated in the model",
                    explanation=f"{connection.id} is bidirectional and crosses {where}; its authentication "
                    f"describes how {connection.source_id} authenticates to {connection.target_id}, not the "
                    "reverse direction.",
                    recommendation="Model the reverse flow as its own connection with its controls.",
                    evidence=shown,
                    **common,
                )
            )
        if sensitive:
            classified: list[tuple[ElementFacts, list[str]]] = [
                (facts, list(CLASSIFICATION)) for facts in (link, *ends)
            ]
            data = tuple(e for facts, props in classified for e in evidence(facts, props))
            safe = protected is True and auth is True
            findings.append(
                self._finding(
                    T.SENSITIVE_DATA_CROSSES_BOUNDARY,
                    Severity.LOW if safe else Severity.MEDIUM,
                    certainty(*classified, *placed),
                    title=f"Sensitive data crosses a trust boundary over {label}",
                    explanation=f"{connection.id} crosses {where} and carries or reaches data declared "
                    "sensitive. "
                    + (
                        "It declares encryption in transit and authentication."
                        if safe
                        else "Its protection is not fully modeled or not present."
                    ),
                    recommendation="Review which data needs to cross, and the controls on both sides.",
                    evidence=crosses + data,
                    assumptions=(MECHANISM_NOT_VERIFIED,) if safe else (),
                    **common,
                )
            )
        return findings
