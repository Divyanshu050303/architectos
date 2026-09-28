"""Engine integration (ARCH-COMP-001, phase 5): the Validation Engine checks nodes that refer to a
catalog component against their specifications' documented constraints, reports what cannot be
evaluated, records the specification versions it used with the run, and states which nodes it could
not check — while an engine without a catalog, or an architecture without component references,
validates exactly as before."""

import uuid
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.serialization import content_hash
from core.domain.components.repository import ComponentCatalog
from core.domain.components.specifications import ComponentSpecification
from core.domain.validation.options import RevisionInfo, ValidationConfig
from core.domain.validation.results import Finding, Severity, ValidationResult
from core.domain.validation.validation_service import ValidationService
from engines.validation.context import ValidationContext
from engines.validation.service import DeterministicValidationEngine
from persistence.component_catalog import default_catalog
from tests.unit.architecture.world import make_world
from tests.unit.architecture_ir.builders import node
from tests.unit.components.test_component_evaluation import constraint
from tests.unit.components.test_component_specifications import spec
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork
from tests.unit.validation.test_validation_service import architecture, validate

RULE = "configuration.component-constraints"
CATALOG = default_catalog()


def queue(node_id: str, **values: Any) -> Node:
    return node(node_id, NodeKind.QUEUE, component="messaging/aws-sqs", configuration=Configuration(values))


def run(*nodes: Node, catalog: ComponentCatalog | None = CATALOG, **config: Any) -> ValidationResult:
    ir = ArchitectureIR("Queues", nodes=nodes)
    revision = RevisionInfo(str(uuid.UUID(int=9)), 1, content_hash(ir))
    engine = DeterministicValidationEngine(catalog=catalog)
    return engine.validate(ir, revision, requirements=(), policy=None, config=ValidationConfig(**config))


def component_findings(result: ValidationResult) -> dict[str, Finding]:
    return {f.entity_ids[0]: f for f in result.findings if f.rule_id == RULE}


def test_a_violated_limit_is_a_validation_finding_with_its_evidence() -> None:
    result = run(queue("audit", retention_seconds=30), queue("orders", retention_seconds=345_600))
    found = component_findings(result)
    assert set(found) == {"audit"}  # a passing check is not a finding
    violation = found["audit"]
    assert (violation.code, violation.severity) == ("component_constraint_violated", Severity.HIGH)
    assert violation.field_paths == ("configuration.retention_seconds",)
    assert (violation.expected, violation.actual) == ("between 60 and 1209600", "30")
    evidence = {e.label: e.value for e in violation.evidence}
    assert evidence == {
        "check": "retention_period",
        "specification": "messaging/aws-sqs@2",
        "basis": "documented",
    }
    assert result.components == {"messaging/aws-sqs@2": CATALOG.get("messaging/aws-sqs").content_hash}


def test_what_cannot_be_evaluated_stays_visible() -> None:
    result = run(
        queue("silent"),
        node("mystery", NodeKind.DATABASE, component="databases/quantum-db"),
        node("planned", NodeKind.DATABASE, component="databases/mysql"),
    )
    found = component_findings(result)
    assert {k: (f.code, f.severity) for k, f in found.items()} == {
        "silent": ("component_check_not_evaluable", Severity.INFO),
        "mystery": ("component_not_in_catalog", Severity.INFO),
        "planned": ("component_not_specified", Severity.INFO),
    }


def test_the_limitation_names_what_was_not_checked() -> None:
    codes = {x.code for x in run(queue("audit", retention_seconds=30), node("api")).limitations}
    assert "components_not_referenced" in codes  # api refers to no component
    assert "catalog_unavailable" not in codes
    assert "catalog_unavailable" in {x.code for x in run(queue("audit"), catalog=None).limitations}
    only_referenced = {x.code for x in run(queue("audit", retention_seconds=100)).limitations}
    assert not {"catalog_unavailable", "components_not_referenced"} & only_referenced


def test_without_a_catalog_or_references_validation_is_unchanged() -> None:
    without = run(queue("audit", retention_seconds=30), catalog=None)
    assert component_findings(without) == {}
    assert without.components == {}
    plain = ArchitectureIR("Plain", nodes=(node("api"),))
    revision = RevisionInfo(str(uuid.UUID(int=9)), 1, content_hash(plain))
    with_catalog = ValidationContext(plain, revision, catalog=CATALOG)
    assert with_catalog.fingerprint == ValidationContext(plain, revision).fingerprint  # same inputs, same id
    assert with_catalog.components is None


def test_checks_of_one_property_with_one_outcome_are_one_finding() -> None:
    two_limits = ComponentSpecification.from_dict(
        spec(
            constraints=[constraint("first", limit=100), constraint("second", limit=200, severity="critical")]
        )
    )
    catalog = ComponentCatalog.build([two_limits], {two_limits.ref: two_limits.content_hash})
    db = node(
        "db",
        NodeKind.DATABASE,
        component="databases/example-sql",
        configuration=Configuration({"max_connections": 500}),
    )
    [merged] = component_findings(run(db, catalog=catalog)).values()
    assert merged.severity is Severity.CRITICAL  # the most severe of the checks
    assert [e.value for e in merged.evidence if e.label == "check"] == ["first", "second"]
    assert merged.expected == "at most 100; at most 200"


def test_the_rule_needs_the_catalog_and_can_be_deselected() -> None:
    [meta] = [r for r in DeterministicValidationEngine().rules() if r["id"] == RULE]
    assert meta["inputs"] == ["catalog"]
    deselected = run(queue("audit", retention_seconds=30), rules=("structure.disconnected-component",))
    assert component_findings(deselected) == {}
    assert deselected.components == {}  # nothing was checked against a specification


async def test_a_stored_run_records_the_specification_versions() -> None:
    clock = FakeClock()
    uow = FakeUnitOfWork(clock)
    world = await make_world(uow, clock)
    service = ValidationService(uow, DeterministicValidationEngine(catalog=CATALOG), clock=clock)
    ir = ArchitectureIR("Queues", nodes=(queue("audit", retention_seconds=30),))
    aid = await architecture(uow, clock, world, ir)
    report = await validate(service, world, aid)
    assert report.inputs.components == {"messaging/aws-sqs@2": CATALOG.get("messaging/aws-sqs").content_hash}
    assert report.inputs.to_dict()["components"] == dict(report.inputs.components)
