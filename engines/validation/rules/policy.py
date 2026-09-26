"""Policy rules: the project's architecture policy (core/domain/projects/policies.py), one rule
per policy field. They are mandatory (a request can neither deselect nor re-grade them) and a
violation is blocking. A value the architecture does not state is reported separately, without
blocking: it may comply, it cannot be shown to."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from core.architecture_ir.component import NodeKind
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.validation.results import Category, Finding, Severity

from ..context import ValidationContext
from ..engine import Input, Outcome, RuleMeta
from ..findings import finding
from .consistency import ALL_PROFILES

# Protocols that are encrypted by definition (the protocol name says so), so tls need not be stated.
ENCRYPTED_PROTOCOLS = frozenset({"https", "wss", "amqps", "mqtts", "rediss", "ldaps", "ftps", "smtps"})
# Nodes that need not state a technology: they are not ours to choose, or not components.
_TECHNOLOGY_OPTIONAL = frozenset({NodeKind.CLIENT, NodeKind.EXTERNAL, NodeKind.BOUNDARY})


def _meta(rule_id: str, name: str, description: str) -> RuleMeta:
    return RuleMeta(
        rule_id,
        1,
        name,
        description,
        Category.POLICY,
        Severity.HIGH,
        profiles=ALL_PROFILES,
        inputs=frozenset({Input.POLICY}),
        mandatory=True,
    )


def _policy(context: ValidationContext) -> ArchitecturePolicy | None:
    return context.policy if context.has_policy else None


@dataclass(frozen=True, slots=True)
class Technologies:
    meta = _meta(
        "policy.technology",
        "Technologies allowed by policy",
        "No prohibited technology is used and, when the policy lists allowed technologies, only those are.",
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        policy = _policy(context)
        if policy is None or not (policy.allowed_technologies or policy.prohibited_technologies):
            return Outcome()
        return Outcome(tuple(self._findings(context, policy)))

    def _findings(self, context: ValidationContext, policy: ArchitecturePolicy) -> Iterator[Finding]:
        allowed = policy.allowed_technologies
        for node in context.ir.nodes:
            if node.technology is None:
                if allowed and node.kind not in _TECHNOLOGY_OPTIONAL:
                    yield finding(
                        self.meta,
                        "technology_unstated",
                        title=f"{node.name} does not state its technology",
                        explanation=(
                            "The policy allows only some technologies, and this component does not "
                            "say which one it uses, so it cannot be shown to comply."
                        ),
                        remediation="State the component's technology.",
                        entity_ids=[node.id],
                        field_paths=["technology"],
                        severity=Severity.LOW,
                        policy_rule="allowed_technologies",
                    )
                continue
            name = node.technology.name
            if name in policy.prohibited_technologies:
                yield finding(
                    self.meta,
                    "prohibited_technology",
                    title=f"{node.name} uses {name}, which the project prohibits",
                    explanation=f"The project's architecture policy prohibits {name}.",
                    remediation="Replace it with a technology the policy allows.",
                    entity_ids=[node.id],
                    field_paths=["technology.name"],
                    expected=f"not {name}",
                    actual=name,
                    blocking=True,
                    policy_rule="prohibited_technologies",
                )
            elif allowed and name not in allowed:
                shown = ", ".join(sorted(allowed)[:20]) + (", …" if len(allowed) > 20 else "")
                yield finding(
                    self.meta,
                    "technology_not_allowed",
                    title=f"{node.name} uses {name}, which the project does not allow",
                    explanation=f"The project's architecture policy allows only: {shown}.",
                    remediation="Use an allowed technology, or have the policy extended.",
                    entity_ids=[node.id],
                    field_paths=["technology.name"],
                    expected=f"one of: {shown}",
                    actual=name,
                    blocking=True,
                    policy_rule="allowed_technologies",
                )


@dataclass(frozen=True, slots=True)
class Regions:
    meta = _meta(
        "policy.region",
        "Regions allowed by policy",
        "Every stated region (of a node or a boundary) is one the policy allows. Availability "
        "zones are not interpreted: their naming differs between providers.",
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        policy = _policy(context)
        if policy is None or not policy.allowed_regions:
            return Outcome()
        return Outcome(tuple(self._findings(context, policy.allowed_regions)))

    def _findings(self, context: ValidationContext, allowed: frozenset[str]) -> Iterator[Finding]:
        shown = ", ".join(sorted(allowed)[:20]) + (", …" if len(allowed) > 20 else "")
        for node in context.ir.nodes:
            region = node.configuration.get("region")
            if node.configuration.is_unknown("region"):
                yield finding(
                    self.meta,
                    "region_unknown",
                    title=f"The region of {node.name} is unknown",
                    explanation="The policy restricts regions, and this element's region is not known.",
                    remediation="State the region, or confirm it from the running system.",
                    entity_ids=[node.id],
                    field_paths=["configuration.region"],
                    severity=Severity.LOW,
                    policy_rule="allowed_regions",
                )
            elif isinstance(region, str) and region not in allowed:
                yield finding(
                    self.meta,
                    "region_not_allowed",
                    title=f"{node.name} is placed in {region}, which the project does not allow",
                    explanation=f"The project's architecture policy allows only: {shown}.",
                    remediation="Move it to an allowed region, or have the policy extended.",
                    entity_ids=[node.id],
                    field_paths=["configuration.region"],
                    expected=f"one of: {shown}",
                    actual=region,
                    blocking=True,
                    policy_rule="allowed_regions",
                )


@dataclass(frozen=True, slots=True)
class EncryptionInTransit:
    meta = _meta(
        "policy.tls",
        "Encryption in transit",
        "When the policy requires TLS, every communicating connection is encrypted: tls is true, "
        "or its protocol is encrypted by definition (https, wss, …).",
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        policy = _policy(context)
        if policy is None or not policy.require_tls:
            return Outcome()
        return Outcome(tuple(self._findings(context)))

    def _findings(self, context: ValidationContext) -> Iterator[Finding]:
        for c in context.ir.connections:
            if not c.kind.communicates:
                continue
            tls = c.configuration.get("tls")
            label = c.name or c.id
            if tls is False:
                yield finding(
                    self.meta,
                    "tls_disabled",
                    title=f"{label} is not encrypted in transit",
                    explanation="The project requires TLS on every connection; this one has tls false.",
                    remediation="Enable TLS on the connection.",
                    entity_ids=[c.id],
                    field_paths=["configuration.tls"],
                    expected="true",
                    actual="false",
                    blocking=True,
                    policy_rule="require_tls",
                )
            elif tls is None and c.protocol not in ENCRYPTED_PROTOCOLS:
                unknown = c.configuration.is_unknown("tls")
                yield finding(
                    self.meta,
                    "tls_unstated",
                    title=f"{label} does not show that it is encrypted",
                    explanation=(
                        "The project requires TLS on every connection; "
                        + ("whether this one uses it is unknown." if unknown else "this one does not say.")
                    ),
                    remediation="State tls for the connection (or use an encrypted protocol).",
                    entity_ids=[c.id],
                    field_paths=["configuration.tls"],
                    expected="true",
                    actual="unknown" if unknown else "not stated",
                    severity=Severity.MEDIUM,
                    policy_rule="require_tls",
                )


@dataclass(frozen=True, slots=True)
class ComponentCount:
    meta = _meta(
        "policy.component-count",
        "Component count",
        "The architecture has at most as many components as the policy allows (boundaries are not "
        "components).",
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        policy = _policy(context)
        count = len(context.topology.components())
        if policy is None or policy.max_components is None or count <= policy.max_components:
            return Outcome()
        excess = finding(
            self.meta,
            "too_many_components",
            title=f"{count} components, more than the {policy.max_components} the project allows",
            explanation="The project's architecture policy caps the number of components.",
            remediation="Consolidate components, or have the limit raised.",
            field_paths=["nodes"],
            expected=f"at most {policy.max_components}",
            actual=str(count),
            evidence=[("components", str(count)), ("limit", str(policy.max_components))],
            blocking=True,
            policy_rule="max_components",
        )
        return Outcome((excess,))


RULES = (Technologies(), Regions(), EncryptionInTransit(), ComponentCount())
