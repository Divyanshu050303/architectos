"""Authentication analysis: architecture-level identity controls, as the architecture models them.
This is about the analyzed system's components, never about ArchitectOS's own sign-in.

- **Components.** A component needs its callers to authenticate when it is declared ``public``, when
  it performs ``sensitive_operations``, or when it declares an ``authorization`` model (which needs an
  identity to decide on). If it then declares ``authentication: none``, that is a modeled gap
  (``missing_authentication``); if it does not declare authentication at all, it cannot be evaluated
  (``authentication_not_modeled``). An absent property is never read as "none".
- **Service identity.** A connection from a component (not a client) that declares
  ``authentication: none`` carries no service identity (``unauthenticated_connection``); a connection
  into a component that requires authentication or holds sensitive data, without a declared
  mechanism, cannot be evaluated. Crossings the trust-boundary analyzer already reported are not
  reported again.
- **Consistency.** When some modeled paths into a component authenticate and others declare
  ``none`` (or the component declares a mechanism that an incoming path does not use), the weakest
  path decides what the control is worth (``inconsistent_authentication``, a potential risk).

A mechanism's name is what the architecture says: it is never taken as proof of a correct
implementation, and no credential or configuration value is read or shown.
"""

from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.facts import ElementFacts
from core.domain.security.inputs import ComponentSecurity
from core.domain.security.results import FindingCategory, FindingType, SecurityFinding
from core.domain.validation.results import Severity

from .context import SecurityContext
from .engine import AnalyzerMeta, AnalyzerOutput, Progress, names
from .support import MECHANISM_NOT_VERIFIED, authenticated, certainty, evidence, finding

T = FindingType
COMPONENT_FACTS = (
    "exposure",
    "authentication",
    "authorization",
    "sensitive_operations",
    "data_classification",
    "personal_data",
)
TARGET_FACTS = ("authentication", "sensitive_operations", "data_classification", "personal_data")
# Findings of the trust-boundary analyzer that already cover a connection's authentication.
CROSSING_TYPES = frozenset({T.UNPROTECTED_BOUNDARY_CROSSING, T.CROSSING_CONTROLS_NOT_MODELED})


def _needs(facts: ComponentSecurity) -> list[str]:
    """Why a component's callers must authenticate, by what it declares."""
    reasons = []
    if facts.known("exposure") == "public":
        reasons.append("it is public")
    if facts.known("sensitive_operations") is True:
        reasons.append("it performs sensitive operations")
    authorization = facts.known("authorization")
    if authorization not in (None, "none"):
        reasons.append(f"it declares authorization ({authorization}), which needs an identity")
    return reasons


def _sensitive_target(facts: ComponentSecurity) -> bool:
    return facts.sensitive is True or facts.known("sensitive_operations") is True


class Authentication:
    meta = AnalyzerMeta(
        id="authentication",
        version=1,
        name="Authentication",
        description="Whether components that need their callers to authenticate, and the service-to-"
        "service flows into them, model an authentication control.",
        category=FindingCategory.AUTHENTICATION,
        finding_types=(
            T.MISSING_AUTHENTICATION,
            T.AUTHENTICATION_NOT_MODELED,
            T.UNAUTHENTICATED_CONNECTION,
            T.INCONSISTENT_AUTHENTICATION,
        ),
        inputs=("components", "connections", "findings"),
        properties=COMPONENT_FACTS,
        rules=(
            "A component that is public, performs sensitive operations or declares authorization needs "
            "an authentication control: authentication none is a gap, no authentication declared cannot "
            "be evaluated.",
            "A connection from a component declaring authentication none carries no service identity.",
            "A connection into a component that requires authentication or holds sensitive data, "
            "without a declared mechanism, cannot be evaluated.",
            "Paths into one component that authenticate differently, one of them not at all, are a "
            "potential risk.",
        ),
        unsupported=(
            "Whether a declared mechanism is implemented or configured correctly.",
            "User authentication of ArchitectOS itself (a platform concern, not analyzed here).",
        ),
        limitations=(MECHANISM_NOT_VERIFIED,),
    )

    def analyze(self, context: SecurityContext, progress: Progress) -> AnalyzerOutput:
        covered = {c for f in progress.findings if f.type in CROSSING_TYPES for c in f.connection_ids}
        findings = [f for node in context.components if (f := self._component(context, node))]
        findings += self._connections(context, covered)
        findings += self._consistency(context)
        return AnalyzerOutput(tuple(findings))

    def _component(self, context: SecurityContext, node: Node) -> SecurityFinding | None:
        facts = context.facts[node.id]
        reasons = _needs(facts)
        auth = authenticated(facts)
        if not reasons or auth is True:
            return None
        why = " and ".join(reasons)
        used = (facts, COMPONENT_FACTS)
        shown = evidence(facts, COMPONENT_FACTS)
        if auth is False:
            if _sensitive_target(facts):
                severity = Severity.HIGH
            elif reasons == ["it is public"] and facts.known("data_classification") == "public":
                severity = Severity.LOW  # public content, perhaps meant to be open: worth a look
            else:
                severity = Severity.MEDIUM
            return finding(
                self.meta,
                T.MISSING_AUTHENTICATION,
                severity,
                certainty(used),
                title=f"{node.id} accepts unauthenticated requests",
                explanation=f"{node.id} declares authentication none, but {why}: anyone who can reach it "
                "can use it without proving who they are.",
                recommendation=f"Review whether {node.id} should require its callers to authenticate, or "
                "whether it is meant to be open (and holds nothing that needs protecting).",
                node_ids=(node.id,),
                evidence=shown,
            )
        return finding(
            self.meta,
            T.AUTHENTICATION_NOT_MODELED,
            Severity.MEDIUM if facts.known("exposure") == "public" else Severity.LOW,
            certainty(used),
            title=f"How callers authenticate to {node.id} is not modeled",
            explanation=f"{node.id} needs its callers to authenticate ({why}), but the architecture "
            "does not say whether or how they do: it is neither shown protected nor shown open.",
            recommendation=f"State the authentication of {node.id} (none, or its mechanism).",
            node_ids=(node.id,),
            evidence=shown,
            missing=(f"{node.id}.configuration.authentication",),
        )

    def _connections(self, context: SecurityContext, covered: set[str]) -> list[SecurityFinding]:
        findings = []
        for connection in context.connections:
            if not connection.kind.communicates or connection.id in covered:
                continue
            source = context.topology.node(connection.source_id)
            if source is None or source.kind is NodeKind.CLIENT:
                continue  # a client's identity is its target's authentication (above)
            link = context.connection_facts[connection.id]
            target = context.facts[connection.target_id]
            auth = authenticated(link)
            label = f"{connection.id} ({connection.source_id} → {connection.target_id})"
            common: dict[str, Any] = {
                "node_ids": (connection.source_id, connection.target_id),
                "connection_ids": (connection.id,),
                "evidence": evidence(link, ["authentication"]) + evidence(target, TARGET_FACTS),
            }
            used: list[tuple[ElementFacts, tuple[str, ...]]] = [
                (link, ("authentication",)),
                (target, TARGET_FACTS),
            ]
            if auth is False:
                findings.append(
                    finding(
                        self.meta,
                        T.UNAUTHENTICATED_CONNECTION,
                        Severity.HIGH if _sensitive_target(target) else Severity.MEDIUM,
                        certainty(*used),
                        title=f"{connection.source_id} calls {connection.target_id} without an identity",
                        explanation=f"{label} declares authentication none: {connection.target_id} cannot "
                        f"tell {connection.source_id} from any other caller that reaches it.",
                        recommendation=f"Review whether {connection.source_id} should authenticate to "
                        f"{connection.target_id} (e.g. mutual TLS or a service token).",
                        **common,
                    )
                )
            elif auth is None and (_sensitive_target(target) or authenticated(target) is True):
                findings.append(
                    finding(
                        self.meta,
                        T.AUTHENTICATION_NOT_MODELED,
                        Severity.LOW,
                        certainty(*used),
                        title=f"How {connection.source_id} authenticates to {connection.target_id} is "
                        "not modeled",
                        explanation=f"{connection.target_id} requires authentication or holds sensitive "
                        f"data, but {label} does not say how its caller proves who it is.",
                        recommendation=f"State the authentication of {connection.id}.",
                        missing=(f"{connection.id}.configuration.authentication",),
                        **common,
                    )
                )
        return findings

    def _consistency(self, context: SecurityContext) -> list[SecurityFinding]:
        incoming: dict[str, list[str]] = {}
        for connection in context.ir.connections:
            if connection.kind.communicates and connection.target_id in context.component_ids:
                incoming.setdefault(connection.target_id, []).append(connection.id)
        findings = []
        for target_id in sorted(incoming):
            declared = {
                c: value
                for c in sorted(incoming[target_id])
                if (value := context.connection_facts[c].known("authentication")) is not None
            }
            target = context.facts[target_id]
            open_paths = sorted(c for c, v in declared.items() if v == "none")
            mechanisms = sorted({str(v) for v in declared.values() if v != "none"})
            if not open_paths or not (mechanisms or authenticated(target) is True):
                continue
            used = [(target, ("authentication",))] + [
                (context.connection_facts[c], ("authentication",)) for c in declared
            ]
            protected = ", ".join(mechanisms) or str(target.known("authentication"))
            verb = "declares" if len(open_paths) == 1 else "declare"
            findings.append(
                finding(
                    self.meta,
                    T.INCONSISTENT_AUTHENTICATION,
                    Severity.MEDIUM,
                    certainty(*used),
                    title=f"Paths into {target_id} authenticate inconsistently",
                    explanation=f"Some modeled paths into {target_id} authenticate ({protected}), but "
                    f"{names(open_paths)} {verb} none: the weakest path decides what {target_id}'s "
                    "authentication is worth.",
                    recommendation=f"Review whether every path into {target_id} should authenticate the "
                    "same way.",
                    node_ids=(target_id,),
                    connection_ids=tuple(declared),
                    evidence=tuple(e for facts, props in used for e in evidence(facts, props)),
                )
            )
        return findings
