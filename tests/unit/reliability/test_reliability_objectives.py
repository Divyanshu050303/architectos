"""Reliability objectives and requirement traceability (Milestone 9, phase 7): request objectives and
machine-checkable requirements, each satisfied or violated only by modeled evidence, never passed on
missing evidence, with requirement ids kept."""

import dataclasses
import uuid
from decimal import Decimal
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.units import Quantity
from core.domain.reliability.analyses import Objective, ReliabilityAnalysisRequest
from core.domain.reliability.results import FindingType, ObjectiveKind, ObjectiveResult, ReliabilityResult
from core.domain.requirements.entities import Requirement
from core.domain.requirements.enums import (
    RequirementPriority,
    RequirementScope,
    RequirementStatus,
    RequirementType,
)
from core.domain.requirements.value_objects import Operator, QuantityConstraint
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity, Verdict
from engines.reliability.context import ReliabilityContext
from engines.reliability.engine import analyze
from engines.reliability.registry import default_registry
from tests.unit.architecture_ir.builders import connection, node
from tests.unit.validation.test_requirement_rules import ref, requirement

REVISION = RevisionInfo("arch-1", 1, "c" * 64)
SYNC: dict[str, Any] = {
    "kind": ConnectionKind.REQUEST,
    "protocol": "https",
    "interaction": Interaction.SYNCHRONOUS,
}


def shop(**db: Any) -> ArchitectureIR:
    base = {"replicas": 1, "mtbf_seconds": 99, "mttr_seconds": 1, "backup_interval_seconds": 3600}
    values = {k: v for k, v in (base | db).items() if v is not None}
    return ArchitectureIR(
        "Shop",
        nodes=(
            node("web", NodeKind.CLIENT),
            node("api", configuration=Configuration({"availability": Decimal("0.999"), "mttr_seconds": 300})),
            node("db", NodeKind.DATABASE, configuration=Configuration(values)),
            node("files", NodeKind.STORAGE),
        ),
        connections=(
            connection("web-api", "web", "api", **SYNC),
            connection(
                "api-db",
                "api",
                "db",
                **(SYNC | {"kind": ConnectionKind.DATA_ACCESS, "protocol": "postgresql"}),
            ),
        ),
    )


def run(
    ir: ArchitectureIR, *objectives: Objective, requirements: tuple[Requirement, ...] = ()
) -> ReliabilityResult:
    request = ReliabilityAnalysisRequest(uuid.UUID(int=1), 1, objectives=objectives)
    return analyze(ReliabilityContext(ir, REVISION, request, requirements), default_registry())


def verdict(result: ReliabilityResult, key: str) -> ObjectiveResult:
    return next(o for o in result.objectives if o.key == key)


def need(number: int, metric: str, operator: str, value: Any, unit: str, **overrides: Any) -> Requirement:
    kind = (
        RequirementType.AVAILABILITY if metric in {"availability", "uptime"} else RequirementType.RELIABILITY
    )
    return requirement(
        number, type=kind, category=metric, title=f"{metric} objective", statement=f"The {metric} objective.",
        structured_data={"metric": metric, "operator": operator, "value": value, "unit": unit}, **overrides,
    )  # fmt: skip


# --- request objectives --------------------------------------------------------------------------


def test_availability_on_paths_satisfied_violated_or_unknown() -> None:
    # web path: api 0.999 x db 0.99 = 0.98901
    satisfied = verdict(
        run(shop(), Objective("slo", ObjectiveKind.AVAILABILITY, target=Decimal("0.98"))), "slo"
    )
    assert (satisfied.verdict, satisfied.target, satisfied.node_ids) == (
        Verdict.SATISFIED,
        ">= 0.98",
        ("web",),
    )
    assert [(e.label, e.value) for e in satisfied.actual] == [("web.availability", "0.98901")]
    result = run(shop(), Objective("slo", ObjectiveKind.AVAILABILITY, target=Decimal("0.999")))
    missed = verdict(result, "slo")
    assert (missed.verdict, missed.missing) == (Verdict.VIOLATED, ())
    [finding] = [f for f in result.findings if f.type is FindingType.AVAILABILITY_BELOW_OBJECTIVE]
    assert (finding.node_ids, finding.severity, finding.objective) == (("web",), Severity.MEDIUM, "slo")
    unknown = verdict(
        run(shop(mtbf_seconds=None), Objective("slo", ObjectiveKind.AVAILABILITY, target=Decimal("0.9"))),
        "slo",
    )
    assert (unknown.verdict, unknown.missing) == (Verdict.NOT_VERIFIABLE, ("db.availability",))


def test_missing_evidence_is_never_success() -> None:
    result = run(shop(mtbf_seconds=None), Objective("slo", ObjectiveKind.AVAILABILITY, target=Decimal("0.5")))
    assert verdict(result, "slo").verdict is Verdict.NOT_VERIFIABLE
    [finding] = [f for f in result.findings if f.type is FindingType.OBJECTIVE_NOT_EVALUABLE]
    assert (finding.missing, finding.objective) == (("db.availability",), "slo")


def test_a_known_violation_stands_even_when_other_values_are_unknown() -> None:
    objective = Objective(
        "c", ObjectiveKind.AVAILABILITY, target=Decimal("0.9995"), node_ids=("api", "files")
    )
    result = verdict(run(shop(), objective), "c")
    assert (result.verdict, result.missing) == (Verdict.VIOLATED, ("files.availability",))


def test_recovery_time_objective() -> None:
    rto = Objective("rto", ObjectiveKind.RECOVERY_TIME, duration=Quantity.of("5", "min"))
    result = run(shop(), rto)
    assert verdict(result, "rto").verdict is Verdict.SATISFIED  # api 300 s, db 1 s
    tight = run(shop(), Objective("rto", ObjectiveKind.RECOVERY_TIME, duration=Quantity.of("299", "s")))
    assert verdict(tight, "rto").verdict is Verdict.VIOLATED
    [finding] = [f for f in tight.findings if f.type is FindingType.RECOVERY_EXCEEDS_OBJECTIVE]
    assert finding.node_ids == ("api",)
    strict = run(
        shop(), Objective("rto", ObjectiveKind.RECOVERY_TIME, duration=Quantity.of("300", "s"), strict=True)
    )
    assert verdict(strict, "rto").verdict is Verdict.VIOLATED  # 300 s is not less than 300 s


def test_data_loss_objective_covers_every_store() -> None:
    rpo = Objective("rpo", ObjectiveKind.DATA_LOSS, duration=Quantity.of("1", "h"))
    result = verdict(run(shop(), rpo), "rpo")
    assert (result.verdict, result.node_ids) == (
        Verdict.NOT_VERIFIABLE,
        ("db", "files"),
    )  # files declares nothing
    only_db = Objective("rpo", ObjectiveKind.DATA_LOSS, duration=Quantity.of("30", "min"), node_ids=("db",))
    assert verdict(run(shop(), only_db), "rpo").verdict is Verdict.VIOLATED  # backups every hour


def test_redundancy_objective() -> None:
    redundant = Objective("n", ObjectiveKind.REDUNDANCY, target=Decimal(2), node_ids=("db",))
    assert verdict(run(shop(), redundant), "n").verdict is Verdict.VIOLATED
    assert verdict(run(shop(replicas=3), redundant), "n").verdict is Verdict.SATISFIED
    everywhere = verdict(run(shop(), Objective("n", ObjectiveKind.REDUNDANCY, target=Decimal(1))), "n")
    assert (everywhere.verdict, everywhere.missing) == (Verdict.NOT_VERIFIABLE, ("api.replicas",))


def test_an_objective_with_nothing_to_check_is_not_applicable() -> None:
    ir = ArchitectureIR(
        "Shop",
        nodes=(node("web", NodeKind.CLIENT), node("api")),
        connections=(connection("web-api", "web", "api", **SYNC),),
    )
    result = verdict(
        run(ir, Objective("rpo", ObjectiveKind.DATA_LOSS, duration=Quantity.of("1", "h"))), "rpo"
    )
    assert result.verdict is Verdict.NOT_APPLICABLE


# --- requirements --------------------------------------------------------------------------------


def test_machine_checkable_requirements_become_objectives_with_their_ids() -> None:
    uptime = need(1, "availability", ">=", "99.9", "%", priority=RequirementPriority.CRITICAL)
    rto = need(2, "rto", "<=", 10, "min")
    rpo = need(3, "rpo", "<", 1, "h", scope=RequirementScope.DATABASE)
    result = run(shop(), requirements=(uptime, rto, rpo))
    by_key = {o.key: o for o in result.objectives}
    assert set(by_key) == {"requirement.req-1", "requirement.req-2", "requirement.req-3"}
    assert by_key["requirement.req-1"].requirement_id == str(uptime.id)
    assert (by_key["requirement.req-1"].verdict, by_key["requirement.req-1"].target) == (
        Verdict.VIOLATED,
        ">= 0.999",
    )
    assert by_key["requirement.req-2"].verdict is Verdict.SATISFIED
    assert (by_key["requirement.req-3"].verdict, by_key["requirement.req-3"].node_ids) == (
        Verdict.VIOLATED,
        ("db",),
    )
    [finding] = [f for f in result.findings if f.objective == "requirement.req-1"]
    assert finding.severity is Severity.CRITICAL  # the requirement's priority


def test_requirements_referenced_by_components_are_checked_on_them() -> None:
    uptime = need(1, "availability", ">=", "99.95", "%")
    ir = shop()
    nodes = tuple(
        dataclasses.replace(n, requirement_refs=(ref(1),)) if n.id == "api" else n for n in ir.nodes
    )
    referenced = dataclasses.replace(ir, nodes=nodes)
    result = verdict(run(referenced, requirements=(uptime,)), "requirement.req-1")
    assert (result.node_ids, result.verdict) == (("api",), Verdict.VIOLATED)  # 0.999 < 0.9995


def test_requirements_that_cannot_be_checked_are_never_passed() -> None:
    # Both are refused when created today; stored history is loaded as it was, so they can exist.
    stated = need(1, "rto", "<=", 10, "min")
    words = dataclasses.replace(stated, content=dataclasses.replace(stated.content, constraint=None))
    durability = need(2, "durability", ">=", "99.999999999", "%")
    floor = need(3, "availability", ">=", "99.9", "%")
    assert isinstance(floor.content.constraint, QuantityConstraint)
    ceiling_constraint = dataclasses.replace(floor.content.constraint, operator=Operator.AT_MOST)
    ceiling = dataclasses.replace(
        floor, content=dataclasses.replace(floor.content, constraint=ceiling_constraint)
    )
    users = need(4, "availability", ">=", "99", "%", scope=RequirementScope.USER)
    queues = need(5, "availability", ">=", "99", "%", scope=RequirementScope.QUEUE)
    draft = need(6, "availability", ">=", "99", "%", status=RequirementStatus.DRAFT)
    result = run(shop(), requirements=(words, durability, ceiling, users, queues, draft))
    verdicts = {o.key: (o.kind, o.verdict) for o in result.objectives}
    assert verdicts == {
        "requirement.req-1": (ObjectiveKind.UNSUPPORTED, Verdict.NOT_VERIFIABLE),  # words only
        "requirement.req-2": (ObjectiveKind.UNSUPPORTED, Verdict.NOT_VERIFIABLE),  # no durability model
        "requirement.req-3": (ObjectiveKind.UNSUPPORTED, Verdict.NOT_VERIFIABLE),  # a ceiling on availability
        "requirement.req-4": (ObjectiveKind.UNSUPPORTED, Verdict.NOT_VERIFIABLE),  # a user scope
        "requirement.req-5": (ObjectiveKind.UNSUPPORTED, Verdict.NOT_APPLICABLE),  # no queue
    }  # the draft is not in force
    assert "Stated in words only" in verdict(result, "requirement.req-1").explanation
    assert not any(o.verdict is Verdict.SATISFIED for o in result.objectives)


def test_objectives_are_deterministic() -> None:
    requirements = (need(2, "rto", "<=", 10, "min"), need(1, "availability", ">=", "99", "%"))
    first = run(shop(), requirements=requirements)
    second = run(shop(), requirements=tuple(reversed(requirements)))
    assert [o.to_dict() for o in first.objectives] == [o.to_dict() for o in second.objectives]
    assert first.context_fingerprint == second.context_fingerprint
