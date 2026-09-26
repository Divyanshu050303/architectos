"""Requirement rules: traceability between the architecture and the project's requirements, and a
verdict for every requirement in force.

A verdict is only as strong as the data behind it:

- **satisfied**: every element the requirement concerns states a value, and every value complies;
- **violated**: at least one stated value does not comply (the offending elements are named);
- **not_verifiable**: the architecture does not state enough to decide, or the requirement is not
  one validation can check (latency, throughput, availability need the capacity and simulation
  engines). Never a pass;
- **not_applicable**: the architecture has nothing the requirement concerns (e.g. its scope is
  queues and there are none).

What is checked (deterministically, from structured data only):

- ``regions`` (operational regions, compliance data residency): the effective region of each
  concerned element (its own ``region``, else that of the nearest boundary containing it) is one of
  the required regions. Data residency concerns the elements holding data;
- ``storage``: the provisioned ``storage_bytes`` of the databases and object stores, summed;
- ``retention``: the ``retention_seconds`` of every element that states one;
- encryption in transit: a security requirement of category ``encryption`` whose statement names
  transit, TLS, SSL or HTTPS. Every communicating connection has ``tls`` true or an encrypted
  protocol. (Encryption at rest is not described by the architecture schema.)
"""

import re
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.requirements.entities import Requirement
from core.domain.requirements.enums import RequirementPriority, RequirementScope, RequirementType
from core.domain.requirements.requirements import IN_FORCE
from core.domain.requirements.value_objects import (
    Operator,
    QuantityConstraint,
    RangeConstraint,
    SetConstraint,
    decimal_to_str,
)
from core.domain.validation.results import Category, Finding, RequirementResult, Severity, Verdict

from ..context import ValidationContext
from ..engine import Input, Outcome, RuleMeta
from ..findings import finding, verdict
from .consistency import ALL_PROFILES, capped
from .policy import ENCRYPTED_PROTOCOLS

K = NodeKind
_DEPLOYED = frozenset(NodeKind) - {K.CLIENT, K.EXTERNAL, K.BOUNDARY}
_HOLDS_DATA = frozenset({K.DATABASE, K.CACHE, K.STORAGE, K.QUEUE, K.OBSERVABILITY})
_STORES = frozenset({K.DATABASE, K.STORAGE})
_SCOPE_KINDS: dict[RequirementScope, frozenset[NodeKind]] = {
    RequirementScope.SERVICE: frozenset({K.SERVICE, K.WORKER}),
    RequirementScope.API: frozenset({K.SERVICE, K.GATEWAY}),
    RequirementScope.DATABASE: frozenset({K.DATABASE}),
    RequirementScope.QUEUE: frozenset({K.QUEUE}),
    RequirementScope.DATA: _HOLDS_DATA,
}
_IN_TRANSIT = re.compile(r"\b(transit|tls|ssl|https)\b", re.IGNORECASE)
_SEVERITY = {
    RequirementPriority.CRITICAL: Severity.CRITICAL,
    RequirementPriority.HIGH: Severity.HIGH,
    RequirementPriority.MEDIUM: Severity.MEDIUM,
    RequirementPriority.LOW: Severity.LOW,
}
MS_PER_SECOND = 1000


def _meta(rule_id: str, name: str, description: str, severity: Severity) -> RuleMeta:
    return RuleMeta(
        rule_id,
        1,
        name,
        description,
        Category.REQUIREMENTS,
        severity,
        profiles=ALL_PROFILES,
        inputs=frozenset({Input.REQUIREMENTS}),
    )


# --- traceability --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Traceability:
    meta = _meta(
        "requirements.traceability",
        "Requirement traceability",
        "Every requirement in force is referenced by the architecture, and every reference is to a "
        "live requirement in force, at its current version when pinned.",
        Severity.MEDIUM,
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        if context.requirements is None:
            return Outcome()
        return Outcome(tuple(self._findings(context, context.requirements)))

    def _findings(self, context: ValidationContext, requirements: Iterable[Requirement]) -> Iterator[Finding]:
        live = {r.id: r for r in requirements}
        ir = context.ir
        referencing: dict[uuid.UUID, list[tuple[str | None, int | None]]] = {}
        for element_id, refs in [
            (None, ir.requirement_refs),
            *((n.id, n.requirement_refs) for n in ir.nodes),
            *((c.id, c.requirement_refs) for c in ir.connections),
        ]:
            for ref in refs:
                referencing.setdefault(ref.requirement_id, []).append((element_id, ref.version))
        for requirement in sorted(live.values(), key=lambda r: r.number):
            if requirement.content.status in IN_FORCE and requirement.id not in referencing:
                yield finding(
                    self.meta,
                    "unreferenced_requirement",
                    title=f"{requirement.reference} is not traced to the architecture",
                    explanation=(
                        f"{requirement.reference} ({requirement.content.title}) is in force, but "
                        "neither the architecture nor any of its elements references it, so "
                        "nothing shows which part of the design answers it."
                    ),
                    remediation="Reference the requirement from the elements that address it.",
                    field_paths=["requirement_refs"],
                    requirement_id=str(requirement.id),
                )
        for requirement_id in sorted(referencing, key=str):
            yield from self._reference_findings(requirement_id, referencing[requirement_id], live)

    def _reference_findings(
        self,
        requirement_id: uuid.UUID,
        refs: list[tuple[str | None, int | None]],
        live: Mapping[uuid.UUID, Requirement],
    ) -> Iterator[Finding]:
        elements = capped([e for e, _ in refs if e is not None])
        requirement = live.get(requirement_id)
        if requirement is None:
            yield finding(
                self.meta,
                "unknown_requirement_reference",
                title="The architecture references a requirement the project does not have",
                explanation=(
                    f"Requirement {requirement_id} is referenced but is not a live requirement of "
                    "this project (it was deleted, or never belonged to it)."
                ),
                remediation="Remove the reference, or reference the requirement that replaced it.",
                entity_ids=elements,
                field_paths=["requirement_refs"],
                severity=Severity.LOW,
                evidence=[("requirement", str(requirement_id))],
            )
            return
        if requirement.content.status not in IN_FORCE:
            yield finding(
                self.meta,
                "reference_not_in_force",
                title=f"{requirement.reference} is referenced but {requirement.content.status}",
                explanation=(
                    f"{requirement.reference} is {requirement.content.status}, not in force, so it "
                    "does not constrain the architecture."
                ),
                remediation="Remove the reference, or activate the requirement.",
                entity_ids=elements,
                field_paths=["requirement_refs"],
                severity=Severity.LOW,
                requirement_id=str(requirement.id),
            )
        pinned = sorted({v for _, v in refs if v is not None and v < requirement.version})
        if pinned:
            yield finding(
                self.meta,
                "outdated_requirement_reference",
                title=f"The architecture references an older version of {requirement.reference}",
                explanation=(
                    f"The reference is pinned to version {pinned[0]}; {requirement.reference} is now "
                    f"at version {requirement.version}, so the design answers a statement that has "
                    "since changed."
                ),
                remediation="Review the design against the current version and update the reference.",
                entity_ids=elements,
                field_paths=["requirement_refs"],
                severity=Severity.INFO,
                requirement_id=str(requirement.id),
                expected=f"version {requirement.version}",
                actual=f"version {pinned[0]}",
            )


# --- verdicts ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Judged:
    verdict: Verdict
    reason: str
    entity_ids: tuple[str, ...] = ()
    evidence: tuple[tuple[str, str], ...] = ()
    violation: str | None = None  # the finding's explanation, when violated


def _complies(value: Decimal, constraint: QuantityConstraint | RangeConstraint) -> bool:
    if isinstance(constraint, RangeConstraint):
        low, high = (
            constraint.unit.to_canonical(constraint.minimum),
            constraint.unit.to_canonical(constraint.maximum),
        )
        return low <= value <= high
    target = constraint.canonical_value
    match constraint.operator:
        case Operator.AT_LEAST:
            return value >= target
        case Operator.MORE_THAN:
            return value > target
        case Operator.AT_MOST:
            return value <= target
        case Operator.LESS_THAN:
            return value < target
        case _:
            return value == target


def _bound(constraint: QuantityConstraint | RangeConstraint) -> str:
    unit = constraint.unit.symbol
    if isinstance(constraint, RangeConstraint):
        return f"between {decimal_to_str(constraint.minimum)} and {decimal_to_str(constraint.maximum)} {unit}"
    return f"{constraint.operator.value} {decimal_to_str(constraint.value)} {unit}"


@dataclass(frozen=True, slots=True)
class Verdicts:
    meta = _meta(
        "requirements.verdicts",
        "Requirement verdicts",
        "Each requirement in force gets a verdict: satisfied, violated, not verifiable or not "
        "applicable, from the architecture's stated values only.",
        Severity.HIGH,
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        if context.requirements is None:
            return Outcome()
        results: list[RequirementResult] = []
        findings: list[Finding] = []
        for requirement in sorted(context.requirements, key=lambda r: r.number):
            if requirement.content.status not in IN_FORCE:
                continue
            judged = self._judge(context, requirement)
            results.append(
                verdict(
                    self.meta,
                    requirement_id=str(requirement.id),
                    reference=requirement.reference,
                    requirement_version=requirement.version,
                    verdict=judged.verdict,
                    reason=judged.reason,
                    entity_ids=capped(judged.entity_ids),
                    evidence=judged.evidence,
                )
            )
            if judged.verdict is Verdict.VIOLATED:
                findings.append(self._violation(requirement, judged))
        return Outcome(tuple(findings), tuple(results))

    def _violation(self, requirement: Requirement, judged: Judged) -> Finding:
        priority = requirement.content.priority
        return finding(
            self.meta,
            "requirement_violated",
            title=f"The architecture violates {requirement.reference}: {requirement.content.title}",
            explanation=judged.violation or judged.reason,
            remediation="Change the elements named here so they comply, or revise the requirement.",
            entity_ids=capped(judged.entity_ids),
            evidence=judged.evidence,
            severity=_SEVERITY[priority],
            blocking=priority is RequirementPriority.CRITICAL,
            requirement_id=str(requirement.id),
        )

    def _judge(self, context: ValidationContext, requirement: Requirement) -> Judged:  # noqa: PLR0911
        content = requirement.content
        constraint = content.constraint
        if isinstance(constraint, SetConstraint) and constraint.metric == "regions":
            data_only = content.category == "data_residency"
            return self._regions(context, requirement, frozenset(constraint.values), data_only=data_only)
        if isinstance(constraint, QuantityConstraint | RangeConstraint):
            if constraint.metric == "storage":
                return self._storage(context, requirement, constraint)
            if constraint.metric == "retention":
                return self._retention(context, requirement, constraint)
            return Judged(
                Verdict.NOT_VERIFIABLE,
                f"{constraint.metric} is not established by validation: it needs the capacity "
                "and simulation engines.",
            )
        if content.type is RequirementType.SECURITY and content.category == "encryption":
            if _IN_TRANSIT.search(content.statement):
                return self._in_transit(context)
            return Judged(
                Verdict.NOT_VERIFIABLE,
                "Encryption at rest is not described by the architecture schema.",
            )
        return Judged(
            Verdict.NOT_VERIFIABLE,
            "The requirement has no structured constraint the architecture can be checked against."
            if constraint is None
            else f"Validation does not check {constraint.metric}.",
        )

    # --- the checks ------------------------------------------------------------------------------

    def _concerned(
        self, context: ValidationContext, requirement: Requirement, default: frozenset[NodeKind]
    ) -> tuple[Node, ...] | Judged:
        scope = requirement.content.scope
        kinds = default & _SCOPE_KINDS.get(scope, default)
        if not kinds:
            # The scope names components this check does not concern (data residency of an API):
            # never widen or swap the concerned kinds, which could pass on the wrong components.
            concerned = " or ".join(sorted(k.value for k in default))
            return Judged(
                Verdict.NOT_VERIFIABLE,
                f"This check concerns {concerned} components; the requirement is scoped to {scope.value}.",
            )
        nodes = context.topology.nodes_of_kind(*sorted(kinds))
        if not nodes:
            return Judged(
                Verdict.NOT_APPLICABLE,
                f"The architecture has no {' or '.join(sorted(k.value for k in kinds))} component.",
            )
        return nodes

    def _effective(self, context: ValidationContext, node: Node, key: str) -> tuple[str | None, bool]:
        """The value of ``key`` on the node or, when it states none, on the nearest boundary that
        contains it; and whether it is unknown."""
        for element in (node, *context.topology.ancestors(node.id)):
            if element.configuration.is_unknown(key):
                return None, True
            value = element.configuration.get(key)
            if isinstance(value, str):
                return value, False
        return None, False

    def _regions(
        self,
        context: ValidationContext,
        requirement: Requirement,
        allowed: frozenset[str],
        *,
        data_only: bool,
    ) -> Judged:
        concerned = self._concerned(context, requirement, _HOLDS_DATA if data_only else _DEPLOYED)
        if isinstance(concerned, Judged):
            return concerned
        outside, unstated = [], []
        for node in concerned:
            region, _ = self._effective(context, node, "region")  # unknown or unstated: no region
            if region is None:
                unstated.append(node.id)
            elif region.lower() not in allowed:
                outside.append((node.id, region))
        required = ", ".join(sorted(allowed))
        if outside:
            listed = ", ".join(f"{n} ({r})" for n, r in outside[:10])
            return Judged(
                Verdict.VIOLATED,
                f"{len(outside)} element(s) are outside the required regions ({required}).",
                tuple(n for n, _ in outside),
                (("required", required), ("outside", listed)),
                f"{requirement.reference} requires {required}; these elements are elsewhere: {listed}.",
            )
        if unstated:
            return Judged(
                Verdict.NOT_VERIFIABLE,
                f"{len(unstated)} element(s) have no known region (nor does a boundary containing them).",
                tuple(unstated),
            )
        return Judged(
            Verdict.SATISFIED, f"Every concerned element is in {required}.", tuple(n.id for n in concerned)
        )

    def _storage(
        self,
        context: ValidationContext,
        requirement: Requirement,
        constraint: QuantityConstraint | RangeConstraint,
    ) -> Judged:
        concerned = self._concerned(context, requirement, _STORES)
        if isinstance(concerned, Judged):
            return concerned
        sizes = {n.id: n.configuration.get("storage_bytes") for n in concerned}
        unstated = [node_id for node_id, size in sizes.items() if not isinstance(size, int)]
        if unstated:
            return Judged(
                Verdict.NOT_VERIFIABLE,
                f"{len(unstated)} data store(s) do not state their provisioned storage.",
                tuple(unstated),
            )
        total = sum(size for size in sizes.values() if isinstance(size, int))
        evidence = (("provisioned", f"{total} B"), ("required", _bound(constraint)))
        ids = tuple(n.id for n in concerned)
        if _complies(Decimal(total), constraint):
            return Judged(
                Verdict.SATISFIED, f"{total} B provisioned, {_bound(constraint)} required.", ids, evidence
            )
        return Judged(
            Verdict.VIOLATED,
            f"{total} B provisioned, {_bound(constraint)} required.",
            ids,
            evidence,
            f"The data stores provision {total} B; {requirement.reference} requires {_bound(constraint)}.",
        )

    def _retention(
        self,
        context: ValidationContext,
        requirement: Requirement,
        constraint: QuantityConstraint | RangeConstraint,
    ) -> Judged:
        stating = [
            n
            for n in context.ir.nodes
            if "retention_seconds" in n.configuration.values
            or n.configuration.is_unknown("retention_seconds")
        ]
        scoped = _SCOPE_KINDS.get(requirement.content.scope)
        if scoped is not None:
            stating = [n for n in stating if n.kind in scoped]
        if not stating:
            return Judged(Verdict.NOT_VERIFIABLE, "No concerned element states how long it keeps data.")
        return self._each(
            requirement,
            constraint,
            stating,
            lambda n: n.configuration.get("retention_seconds"),
            "retention",
        )

    def _each(
        self,
        requirement: Requirement,
        constraint: QuantityConstraint | RangeConstraint,
        nodes: list[Node],
        read: Callable[[Node], object],
        what: str,
    ) -> Judged:
        failing, unknown = [], []
        for node in nodes:
            seconds = read(node)
            if not isinstance(seconds, int):
                unknown.append(node.id)
            elif not _complies(Decimal(seconds * MS_PER_SECOND), constraint):
                failing.append((node.id, seconds))
        required = _bound(constraint)
        if failing:
            listed = ", ".join(f"{n} ({s} s)" for n, s in failing[:10])
            return Judged(
                Verdict.VIOLATED,
                f"{len(failing)} element(s) have a {what} outside {required}.",
                tuple(n for n, _ in failing),
                (("required", required), ("outside", listed)),
                f"{requirement.reference} requires a {what} of {required}; these elements differ: {listed}.",
            )
        if unknown:
            return Judged(
                Verdict.NOT_VERIFIABLE, f"The {what} of {len(unknown)} element(s) is unknown.", tuple(unknown)
            )
        return Judged(
            Verdict.SATISFIED,
            f"Every element stating a {what} keeps data {required}.",
            tuple(n.id for n in nodes),
        )

    def _in_transit(self, context: ValidationContext) -> Judged:
        communicating = [c for c in context.ir.connections if c.kind.communicates]
        if not communicating:
            return Judged(Verdict.NOT_APPLICABLE, "The architecture has no communicating connection.")
        disabled = [c.id for c in communicating if c.configuration.get("tls") is False]
        if disabled:
            return Judged(
                Verdict.VIOLATED,
                f"{len(disabled)} connection(s) have tls false.",
                tuple(disabled),
                (("unencrypted", ", ".join(disabled[:10])),),
                "Encryption in transit is required, but these connections have tls false: "
                + ", ".join(disabled[:10])
                + ".",
            )
        unstated = [
            c.id
            for c in communicating
            if c.configuration.get("tls") is not True and c.protocol not in ENCRYPTED_PROTOCOLS
        ]
        if unstated:
            return Judged(
                Verdict.NOT_VERIFIABLE,
                f"{len(unstated)} connection(s) do not show that they are encrypted.",
                tuple(unstated),
            )
        return Judged(
            Verdict.SATISFIED,
            "Every communicating connection is encrypted (tls true or an encrypted protocol).",
            tuple(c.id for c in communicating),
        )


RULES = (Traceability(), Verdicts())
