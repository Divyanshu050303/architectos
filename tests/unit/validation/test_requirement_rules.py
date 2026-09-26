"""Requirement traceability and verdicts (Milestone 6, phase 5)."""

import dataclasses
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.traceability import RequirementRef
from core.domain.requirements.entities import NewRequirement, Requirement
from core.domain.requirements.enums import (
    RequirementPriority,
    RequirementScope,
    RequirementStatus,
    RequirementType,
)
from core.domain.validation.results import Finding, RequirementResult, Severity, ValidationResult, Verdict
from engines.validation.context import RevisionInfo, ValidationContext
from engines.validation.engine import NO_REQUIREMENTS, validate
from engines.validation.registry import default_registry
from tests.unit.architecture_ir.builders import api_and_postgres, connection, node

NOW = datetime(2026, 9, 26, tzinfo=UTC)
PROJECT = uuid.UUID("01900000-0000-7000-8000-00000000000a")
REVISION = RevisionInfo("arch-1", 1, "e" * 64)


def requirement(
    number: int, version: int = 1, status: RequirementStatus | None = None, **overrides: Any
) -> Requirement:
    fields: dict[str, Any] = {
        "project_id": PROJECT,
        "created_by_user_id": uuid.uuid7(),
        "type": RequirementType.OPERATIONAL,
        "category": "regions",
        "title": "EU only",
        "statement": "Everything runs in the EU.",
        "priority": RequirementPriority.HIGH,
        "status": RequirementStatus.ACTIVE,
        "structured_data": {"metric": "regions", "operator": "in", "values": ["eu-west-1", "eu-central-1"]},
    }
    created = NewRequirement.create(**(fields | overrides))
    content = created.content if status is None else dataclasses.replace(created.content, status=status)
    return Requirement(
        id=uuid.UUID(int=number),
        project_id=PROJECT,
        number=number,
        version=version,
        content=content,
        source=created.source,
        confidence=created.confidence,
        created_by_user_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


def ref(number: int, version: int | None = None) -> RequirementRef:
    return RequirementRef(uuid.UUID(int=number), version)


def run(ir: ArchitectureIR, *requirements: Requirement) -> ValidationResult:
    result = validate(ValidationContext(ir, REVISION, requirements=requirements), default_registry())
    assert result.failures == ()
    return result


def traced(ir: ArchitectureIR, *numbers: int) -> ArchitectureIR:
    """``ir`` referencing the given requirements at architecture level."""
    return dataclasses.replace(ir, requirement_refs=tuple(ref(n) for n in numbers))


def verdict_of(result: ValidationResult, number: int = 1) -> RequirementResult:
    [found] = [r for r in result.requirement_results if r.requirement_id == str(uuid.UUID(int=number))]
    return found


def of(result: ValidationResult, code: str) -> list[Finding]:
    return [f for f in result.findings if f.code == code]


def placed(api: str | None = "eu-west-1", db: str | None = "eu-west-1") -> ArchitectureIR:
    base = api_and_postgres()
    nodes = []
    for original in base.nodes:
        region = {"api": api, "db": db}.get(original.id)
        values: dict[str, Any] = {**original.configuration.values, "region": region}
        nodes.append(
            dataclasses.replace(original, configuration=Configuration(values)) if region else original
        )
    return traced(ArchitectureIR(name=base.name, nodes=tuple(nodes), connections=base.connections), 1)


# --- limitations and scope -----------------------------------------------------------------------


def test_without_requirements_nothing_is_said_and_the_limitation_is_stated() -> None:
    result = validate(ValidationContext(api_and_postgres(), REVISION), default_registry())
    assert result.requirement_results == ()
    assert NO_REQUIREMENTS in result.limitations
    assert NO_REQUIREMENTS not in run(api_and_postgres()).limitations  # () : the project has none


def test_only_requirements_in_force_get_verdicts() -> None:
    result = run(
        traced(api_and_postgres(), 1, 2, 3),
        requirement(1),
        requirement(2, status=RequirementStatus.DRAFT),
        requirement(3, status=RequirementStatus.SATISFIED),
    )
    assert [r.reference for r in result.requirement_results] == ["REQ-1", "REQ-3"]


# --- traceability --------------------------------------------------------------------------------


def test_requirements_in_force_must_be_referenced() -> None:
    result = run(api_and_postgres(), requirement(1), requirement(2, status=RequirementStatus.DRAFT))
    [found] = of(result, "unreferenced_requirement")
    assert (found.requirement_id, found.severity) == (str(uuid.UUID(int=1)), Severity.MEDIUM)


def test_element_references_count_as_traced() -> None:
    base = api_and_postgres()
    nodes = tuple(
        dataclasses.replace(n, requirement_refs=(ref(1),)) if n.id == "api" else n for n in base.nodes
    )
    assert of(run(api_and_postgres(nodes=nodes), requirement(1)), "unreferenced_requirement") == []


def test_references_to_unknown_retired_and_older_requirements() -> None:
    base = api_and_postgres()
    nodes = tuple(
        dataclasses.replace(n, requirement_refs=(ref(9), ref(2, 1))) if n.id == "db" else n
        for n in base.nodes
    )
    ir = dataclasses.replace(api_and_postgres(nodes=nodes), requirement_refs=(ref(3),))
    result = run(ir, requirement(2, version=3), requirement(3, status=RequirementStatus.DEPRECATED))
    [unknown] = of(result, "unknown_requirement_reference")
    assert (unknown.entity_ids, unknown.severity) == (("db",), Severity.LOW)
    [retired] = of(result, "reference_not_in_force")
    assert retired.requirement_id == str(uuid.UUID(int=3))
    [older] = of(result, "outdated_requirement_reference")
    assert (older.expected, older.actual, older.severity) == ("version 3", "version 1", Severity.INFO)


# --- regions -------------------------------------------------------------------------------------


def test_regions_satisfied_violated_and_not_verifiable() -> None:
    assert verdict_of(run(placed(), requirement(1))).verdict is Verdict.SATISFIED

    violated = run(placed(db="us-east-1"), requirement(1))
    assert (verdict_of(violated).verdict, verdict_of(violated).entity_ids) == (Verdict.VIOLATED, ("db",))
    [found] = of(violated, "requirement_violated")
    assert (found.severity, found.blocking, found.entity_ids) == (Severity.HIGH, False, ("db",))

    unstated = verdict_of(run(placed(db=None), requirement(1)))
    assert (unstated.verdict, unstated.entity_ids) == (Verdict.NOT_VERIFIABLE, ("db",))


def test_a_region_is_inherited_from_the_containing_boundary() -> None:
    ir = traced(
        ArchitectureIR(
            name="Placed",
            nodes=(
                node("eu", NodeKind.BOUNDARY, configuration=Configuration({"region": "eu-central-1"})),
                node("api", parent_id="eu"),
                node("db", NodeKind.DATABASE, parent_id="eu"),
            ),
            connections=(connection(),),
        ),
        1,
    )
    assert verdict_of(run(ir, requirement(1))).verdict is Verdict.SATISFIED


def test_data_residency_concerns_only_data_holders() -> None:
    residency = requirement(
        1,
        type=RequirementType.COMPLIANCE,
        category="data_residency",
        structured_data={"metric": "regions", "operator": "in", "values": ["eu-west-1"]},
        priority=RequirementPriority.CRITICAL,
    )
    assert verdict_of(run(placed(api="us-east-1"), residency)).verdict is Verdict.SATISFIED
    violated = run(placed(db="us-east-1"), residency)
    assert verdict_of(violated).verdict is Verdict.VIOLATED
    assert of(violated, "requirement_violated")[0].blocking  # critical priority blocks


def test_a_scope_without_components_is_not_applicable() -> None:
    queues = requirement(1, scope=RequirementScope.QUEUE)
    judged = verdict_of(run(placed(), queues))
    assert (judged.verdict, judged.reason) == (
        Verdict.NOT_APPLICABLE,
        "The architecture has no queue component.",
    )


# --- storage and retention -----------------------------------------------------------------------


def storage(operator: str, value: str) -> Requirement:
    return requirement(
        1,
        type=RequirementType.DATA,
        category="storage",
        structured_data={"metric": "storage", "operator": operator, "value": value, "unit": "GB"},
    )


def test_storage_is_compared_with_the_provisioned_total() -> None:
    ir = traced(api_and_postgres(), 1)  # the database provisions 100 GB
    assert verdict_of(run(ir, storage(">=", "50"))).verdict is Verdict.SATISFIED
    short = verdict_of(run(ir, storage(">=", "500")))
    assert short.verdict is Verdict.VIOLATED
    assert ("provisioned", "100000000000 B") in [(e.label, e.value) for e in short.evidence]


def test_storage_is_not_verifiable_when_a_store_does_not_state_it() -> None:
    ir = traced(api_and_postgres(nodes=(*api_and_postgres().nodes, node("files", NodeKind.STORAGE))), 1)
    judged = verdict_of(run(ir, storage(">=", "50")))
    assert (judged.verdict, judged.entity_ids) == (Verdict.NOT_VERIFIABLE, ("files",))


def retention(operator: str, value: str) -> Requirement:
    return requirement(
        1,
        type=RequirementType.DATA,
        category="retention",
        structured_data={"metric": "retention", "operator": operator, "value": value, "unit": "d"},
    )


def with_queue(**config: Any) -> ArchitectureIR:
    base = api_and_postgres()
    queue = node("events", NodeKind.QUEUE, configuration=Configuration(**config))
    publish = connection("api-events", "api", "events", kind=ConnectionKind.PUBLISH, protocol="kafka")
    return traced(api_and_postgres(nodes=(*base.nodes, queue), connections=(*base.connections, publish)), 1)


def test_retention_is_checked_where_it_is_stated() -> None:
    week = with_queue(values={"retention_seconds": 604_800})
    assert verdict_of(run(week, retention(">=", "7"))).verdict is Verdict.SATISFIED
    assert verdict_of(run(week, retention(">=", "30"))).verdict is Verdict.VIOLATED
    assert verdict_of(run(week, retention("<=", "30"))).verdict is Verdict.SATISFIED
    unknown = verdict_of(run(with_queue(unknown={"retention_seconds"}), retention(">=", "7")))
    assert (unknown.verdict, unknown.entity_ids) == (Verdict.NOT_VERIFIABLE, ("events",))
    nothing = verdict_of(run(traced(api_and_postgres(), 1), retention(">=", "7")))
    assert nothing.verdict is Verdict.NOT_VERIFIABLE


# --- encryption in transit -----------------------------------------------------------------------


def encryption(statement: str = "All traffic is encrypted in transit with TLS.") -> Requirement:
    return requirement(
        1, type=RequirementType.SECURITY, category="encryption", statement=statement, structured_data={}
    )


def tls(value: bool | None) -> ArchitectureIR:
    config = Configuration({"tls": value}) if value is not None else Configuration()
    base = api_and_postgres()
    connections = tuple(
        dataclasses.replace(c, configuration=config) if c.id == "api-db" else c for c in base.connections
    )
    return traced(api_and_postgres(connections=connections), 1)


def test_encryption_in_transit() -> None:
    assert verdict_of(run(tls(True), encryption())).verdict is Verdict.SATISFIED  # the other one is https
    off = verdict_of(run(tls(False), encryption()))
    assert (off.verdict, off.entity_ids) == (Verdict.VIOLATED, ("api-db",))
    assert verdict_of(run(tls(None), encryption())).verdict is Verdict.NOT_VERIFIABLE
    at_rest = verdict_of(run(tls(True), encryption("Customer data is encrypted at rest.")))
    assert (at_rest.verdict, at_rest.reason) == (
        Verdict.NOT_VERIFIABLE,
        "Encryption at rest is not described by the architecture schema.",
    )


# --- everything else -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {
                "type": RequirementType.PERFORMANCE,
                "category": "latency",
                "structured_data": {
                    "metric": "latency",
                    "operator": "<=",
                    "value": "300",
                    "unit": "ms",
                    "percentile": "95",
                },
            },
            "latency is not established by validation: it needs the capacity and simulation engines.",
        ),
        (
            {"type": RequirementType.FUNCTIONAL, "category": "payment", "structured_data": {}},
            "The requirement has no structured constraint the architecture can be checked against.",
        ),
    ],
)
def test_other_requirements_are_not_verifiable_with_a_reason(overrides: dict[str, Any], reason: str) -> None:
    judged = verdict_of(run(traced(api_and_postgres(), 1), requirement(1, **overrides)))
    assert (judged.verdict, judged.reason) == (Verdict.NOT_VERIFIABLE, reason)


def test_verdicts_are_counted_and_deterministic() -> None:
    requirements = (requirement(1), dataclasses.replace(encryption(), id=uuid.UUID(int=2), number=2))
    ir = traced(placed(), 1, 2)
    first, again = run(ir, *requirements), run(ir, *reversed(requirements))
    assert first == again
    assert first.summary.requirements == {
        "satisfied": 1,
        "violated": 0,
        "not_verifiable": 1,
        "not_applicable": 0,
    }
