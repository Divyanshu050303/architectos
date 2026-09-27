"""Checking a scenario against the exact revision it concerns, before anything is calculated: every
element it names exists, every property applies to the element's kind and its value to the
property, every zone or region is declared by some component, every entry is a node — and each part
is assigned its scenario type (``core/domain/simulations/catalog.py``). Deterministic; nothing is
changed; an invalid scenario is refused with the field, the reason and the element, and never
reaches an engine.
"""

from dataclasses import dataclass

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import CONNECTION_PROPERTIES, NODE_PROPERTIES
from core.architecture_ir.model import ArchitectureIR
from core.domain.engine_results import ModelSet
from core.domain.simulations.catalog import (
    BY_ID,
    ScenarioType,
    analyses_of_change,
    type_of_change,
    type_of_failure,
)
from core.domain.simulations.entities import SimulationRequest
from core.domain.simulations.errors import InvalidSimulationRequest
from core.domain.simulations.scenarios import ConfigurationChange, Failure
from core.domain.simulations.values import AnalysisKind, FailureKind

NOT_FAILABLE = frozenset({NodeKind.CLIENT, NodeKind.BOUNDARY})  # demand sources and groupings


def _invalid(field: str, reason: str, element_id: str) -> InvalidSimulationRequest:
    return InvalidSimulationRequest(details={"field": field, "reason": reason, "element_id": element_id})


@dataclass(frozen=True, slots=True)
class Part:
    """One part of the scenario, its type and the analyses that evaluate it."""

    key: str  # "workload", "change:<element>.<property>", "failure:<kind>:<target>"
    type: ScenarioType
    analyses: tuple[AnalysisKind, ...]


@dataclass(frozen=True, slots=True)
class ScenarioPlan:
    """A scenario checked against its revision: its parts, typed, in canonical order."""

    parts: tuple[Part, ...]

    @property
    def types(self) -> ModelSet:
        """The scenario types (and versions) the simulation relies on."""
        return ModelSet.of((p.type.id, p.type.version) for p in self.parts)

    @property
    def analyses(self) -> tuple[AnalysisKind, ...]:
        """Every analysis some part concerns, in the vocabulary's order."""
        concerned = {a for p in self.parts for a in p.analyses}
        return tuple(a for a in AnalysisKind if a in concerned)


def _change(ir: ArchitectureIR, change: ConfigurationChange) -> Part:
    node, connection = ir.node(change.element_id), ir.connection(change.element_id)
    if node is None and connection is None:
        raise _invalid("scenario.changes.element_id", "unknown_element", change.element_id)
    reference = f"{change.element_id}.{change.property}"
    if node is not None:
        spec = NODE_PROPERTIES.get(change.property)
        applies = spec is not None and node.kind.value in spec.applies_to
    else:
        spec = CONNECTION_PROPERTIES.get(change.property)
        applies = spec is not None
    if spec is None or not applies:
        raise _invalid("scenario.changes.property", "not_applicable", reference)
    if change.value is not None and spec.problems(change.value, change.property):
        raise _invalid("scenario.changes.value", "invalid_value", reference)
    scenario_type = type_of_change(change)
    if scenario_type is None:
        raise _invalid("scenario.changes.property", "not_simulated", reference)
    return Part(f"change:{reference}", scenario_type, analyses_of_change(change))


def _declares(ir: ArchitectureIR, name: str, target: str) -> bool:
    for node in ir.nodes:
        value = node.configuration.get(name)
        if value == target or (isinstance(value, tuple) and target in value):
            return True
    return False


def _failure(ir: ArchitectureIR, failure: Failure) -> Part:
    target = failure.target
    match failure.kind:
        case FailureKind.COMPONENT:
            node = ir.node(target)
            if node is None:
                raise _invalid("scenario.failures.target", "unknown_element", target)
            if node.kind in NOT_FAILABLE:
                raise _invalid("scenario.failures.target", "not_a_component", target)
        case FailureKind.CONNECTION:
            if ir.connection(target) is None:
                raise _invalid("scenario.failures.target", "unknown_element", target)
        case FailureKind.ZONE:
            if not _declares(ir, "availability_zones", target):
                raise _invalid("scenario.failures.target", "unknown_zone", target)
        case FailureKind.REGION:
            if not _declares(ir, "region", target):
                raise _invalid("scenario.failures.target", "unknown_region", target)
    scenario_type = type_of_failure(failure)
    return Part(f"failure:{failure.kind.value}:{target}", scenario_type, scenario_type.analyses)


def check_scenario(ir: ArchitectureIR, request: SimulationRequest) -> ScenarioPlan:
    """The request's scenario checked against ``ir`` (the exact revision). InvalidSimulationRequest
    otherwise, naming the first problem in canonical order."""
    for entry in request.entries or ():
        if ir.node(entry) is None:
            raise _invalid("entries", "unknown_element", entry)
    scenario = request.scenario
    parts: list[Part] = []
    if scenario.workload is not None:
        workload = BY_ID["workload"]
        parts.append(Part("workload", workload, workload.analyses))
    parts += [_change(ir, c) for c in scenario.changes]
    parts += [_failure(ir, f) for f in scenario.failures]
    return ScenarioPlan(tuple(parts))
