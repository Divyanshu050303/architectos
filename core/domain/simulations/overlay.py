"""The scenario overlay: an exact architecture revision seen with a scenario applied — in memory only.

- **Configuration changes** are applied with the IR's own edit commands (``apply_commands``: pure,
  all or nothing, element ids preserved, the result checked to be a valid architecture) to a copy
  of the revision, and each value set is stamped with the scenario's provenance (``user_input``,
  ``reference: scenario:<fingerprint>``, ``actor: simulation``), so every engine sees a declared,
  hypothetical value and every result can tell where it came from.
- **Failures** do not remove anything: they resolve to the components and connections unavailable
  in the scenario, from what the revision declares. A zone failure makes unavailable the components
  whose declared zones are all in the zone, leaves those declaring other zones too running (listed
  as losing a zone), and leaves components declaring no zone ``undetermined``; a region failure makes
  unavailable the components declaring the region, and components declaring no region are
  ``undetermined``. Clients and boundaries never fail.
- The **baseline** is the revision itself; the **scenario** architecture is a distinct object with
  its own content hash. The revision is never modified, and nothing is stored but the scenario
  snapshot: re-applying it to the same revision gives the same overlay (``reconstruct`` checks it).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.architecture_ir.commands import (
    Command,
    InvalidArchitectureCommand,
    UpdateConfiguration,
    UpdateConnectionConfiguration,
    apply_commands,
)
from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.architecture_ir.serialization import content_hash
from core.domain.engine_results import Evidence
from core.domain.requirements.value_objects import decimal_to_str
from core.domain.validation.options import RevisionInfo

from .errors import InvalidSimulationRequest
from .scenarios import ConfigurationChange, Scenario
from .values import FailureKind

NEVER_FAIL = frozenset({NodeKind.CLIENT, NodeKind.BOUNDARY})


def _text(value: ConfigValue | None) -> str:
    if value is None:
        return "not declared"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, tuple):
        return ", ".join(value) or "none"
    if isinstance(value, int | str):
        return str(value)
    return decimal_to_str(value)


@dataclass(frozen=True, slots=True)
class AppliedChange:
    """One property as the revision declares it and as the scenario sets it."""

    element_id: str
    property: str
    before: ConfigValue | None
    after: ConfigValue | None

    def evidence(self) -> Evidence:
        return Evidence(f"{self.element_id}.{self.property}", f"{_text(self.before)} -> {_text(self.after)}")


@dataclass(frozen=True, slots=True)
class Overlay:
    revision: RevisionInfo  # the exact revision (the baseline)
    scenario: Scenario
    baseline: ArchitectureIR  # the revision itself, unchanged
    architecture: ArchitectureIR  # the scenario's in-memory copy
    changes: tuple[AppliedChange, ...]
    unavailable_nodes: tuple[str, ...]
    unavailable_connections: tuple[str, ...]
    undetermined_nodes: tuple[str, ...]  # a zone or region failure, but the node declares none
    zone_losses: tuple[tuple[str, str], ...]  # (node, zone) a node keeps running without

    @property
    def scenario_hash(self) -> str:
        """The scenario architecture's content hash (the baseline's is the revision's)."""
        return content_hash(self.architecture)

    def to_dict(self) -> dict[str, Any]:
        """Everything needed to explain and reconstruct the overlay — never the architecture."""
        return {
            "revision": {
                "architecture_id": self.revision.architecture_id,
                "number": self.revision.number,
                "content_hash": self.revision.content_hash,
            },
            "scenario_fingerprint": self.scenario.fingerprint,
            "scenario_content_hash": self.scenario_hash,
            "changes": [
                {"element_id": c.element_id, "property": c.property, "change": c.evidence().value}
                for c in self.changes
            ],
            "unavailable_nodes": list(self.unavailable_nodes),
            "unavailable_connections": list(self.unavailable_connections),
            "undetermined_nodes": list(self.undetermined_nodes),
            "zone_losses": [list(loss) for loss in self.zone_losses],
        }

    def trace(self) -> tuple[Evidence, ...]:
        """How the scenario was applied, step by step, for the result's calculation trace."""
        steps = [Evidence("overlay.revision", f"{self.revision.number} ({self.revision.content_hash})")]
        steps += [
            Evidence(f"overlay.change.{c.element_id}.{c.property}", c.evidence().value) for c in self.changes
        ]
        steps += [
            Evidence(f"overlay.unavailable.{n}", "component unavailable") for n in self.unavailable_nodes
        ]
        steps += [
            Evidence(f"overlay.unavailable.{c}", "connection unavailable")
            for c in self.unavailable_connections
        ]
        steps += [
            Evidence(f"overlay.zone_loss.{n}", f"keeps running without {z}") for n, z in self.zone_losses
        ]
        steps += [
            Evidence(f"overlay.undetermined.{n}", "declares no zone or region: whether it fails is unknown")
            for n in self.undetermined_nodes
        ]
        steps.append(Evidence("overlay.scenario_content_hash", self.scenario_hash))
        return tuple(steps)


def provenance_of(scenario: Scenario) -> Provenance:
    return Provenance(
        ProvenanceSource.USER_INPUT, reference=f"scenario:{scenario.fingerprint[:16]}", actor="simulation"
    )


def _commands(ir: ArchitectureIR, changes: tuple[ConfigurationChange, ...]) -> list[Command]:
    by_element: dict[str, dict[str, ConfigValue | None]] = {}
    for change in changes:
        by_element.setdefault(change.element_id, {})[change.property] = change.value
    commands: list[Command] = []
    for element_id, values in by_element.items():
        if ir.node(element_id) is not None:
            commands.append(UpdateConfiguration(element_id, values))
        else:
            commands.append(UpdateConnectionConfiguration(element_id, values))
    return commands


def configure(
    ir: ArchitectureIR, changes: tuple[ConfigurationChange, ...], provenance: Provenance
) -> ArchitectureIR:
    """``changes`` applied to an in-memory copy of ``ir`` with the IR's own edit commands (all or
    nothing, ids preserved, the result a valid architecture), each value stamped with ``provenance``.
    Raises the IR's own errors (``InvalidArchitectureCommand``, ``InvalidArchitecture``): each caller
    says them in its own terms. Shared by simulation scenarios and evolution candidates."""
    if not changes:
        return ir
    return apply_commands(ir, _commands(ir, changes), provenance=provenance)


def applied(
    ir: ArchitectureIR, architecture: ArchitectureIR, changes: tuple[ConfigurationChange, ...]
) -> tuple[AppliedChange, ...]:
    """Each changed property as ``ir`` declares it and as ``architecture`` sets it."""
    return tuple(
        AppliedChange(
            c.element_id,
            c.property,
            _configuration(ir, c.element_id).get(c.property),
            _configuration(architecture, c.element_id).get(c.property),
        )
        for c in changes
    )


def _configured(ir: ArchitectureIR, scenario: Scenario) -> ArchitectureIR:
    try:
        return configure(ir, scenario.changes, provenance_of(scenario))
    except InvalidArchitectureCommand as error:
        raise InvalidSimulationRequest(
            details={
                "field": "scenario.changes",
                "reason": error.details.get("reason") or "not_applicable",
                "element_id": str(error.details.get("element_id") or ""),
            }
        ) from None
    except InvalidArchitecture as error:  # e.g. more minimum healthy replicas than replicas
        violation = (error.details.get("violations") or [{}])[0]
        raise InvalidSimulationRequest(
            details={
                "field": "scenario.changes",
                "reason": str(violation.get("rule") or "invalid_architecture"),
                "element_id": f"{violation.get('element_id', '')}.{violation.get('field', '')}",
            }
        ) from None


def _configuration(ir: ArchitectureIR, element_id: str) -> Configuration:
    element = ir.node(element_id) or ir.connection(element_id)
    if element is None:
        raise InvalidSimulationRequest(
            details={
                "field": "scenario.changes.element_id",
                "reason": "unknown_element",
                "element_id": element_id,
            }
        )
    return element.configuration


def _declared(value: ConfigValue | None) -> tuple[str, ...]:
    if isinstance(value, tuple):
        return value
    return (value,) if isinstance(value, str) else ()


def apply_scenario(ir: ArchitectureIR, revision: RevisionInfo, scenario: Scenario) -> Overlay:
    """``scenario`` applied to an in-memory copy of ``ir`` (the exact ``revision``)."""
    architecture = _configured(ir, scenario)
    changes = applied(ir, architecture, scenario.changes)
    nodes: set[str] = set()
    connections: set[str] = set()
    undetermined: set[str] = set()
    losses: set[tuple[str, str]] = set()
    components = [n for n in ir.nodes if n.kind not in NEVER_FAIL]
    for failure in scenario.failures:
        match failure.kind:
            case FailureKind.COMPONENT:
                nodes.add(failure.target)
            case FailureKind.CONNECTION:
                connections.add(failure.target)
            case FailureKind.ZONE:
                for node in components:
                    zones = _declared(node.configuration.get("availability_zones"))
                    if not zones:
                        undetermined.add(node.id)
                    elif set(zones) <= {failure.target}:
                        nodes.add(node.id)
                    elif failure.target in zones:
                        losses.add((node.id, failure.target))
            case FailureKind.REGION:
                for node in components:
                    region = node.configuration.get("region")
                    if region is None:
                        undetermined.add(node.id)
                    elif region == failure.target:
                        nodes.add(node.id)
    return Overlay(
        revision=revision,
        scenario=scenario,
        baseline=ir,
        architecture=architecture,
        changes=changes,
        unavailable_nodes=tuple(sorted(nodes)),
        unavailable_connections=tuple(sorted(connections)),
        undetermined_nodes=tuple(sorted(undetermined - nodes)),  # named unavailable elsewhere: unavailable
        zone_losses=tuple(sorted(loss for loss in losses if loss[0] not in nodes)),
    )


def reconstruct(ir: ArchitectureIR, revision: RevisionInfo, stored: Mapping[str, Any]) -> Overlay:
    """The overlay of a stored simulation, re-applied from its scenario snapshot; refused when the
    revision or the scenario architecture no longer match what was stored."""
    scenario = Scenario.from_dict(stored["scenario"])
    overlay = apply_scenario(ir, revision, scenario)
    if stored["overlay"] != overlay.to_dict():
        raise InvalidSimulationRequest(details={"field": "overlay", "reason": "not_reproducible"})
    return overlay
