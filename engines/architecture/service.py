"""The Architecture Engine boundary: what it consumes and what it must produce.

    RequirementSet → Architecture Planning Input → [generator] → ArchitectureProposal (an IR)
      → check_proposal → a person reviews → ArchitectureService.create / replace (a revision)

The engine itself (patterns, component selection, topology) is not implemented yet. This module
fixes the contract every generator (rule-based or model-assisted) must honour:

- its output is the canonical ``ArchitectureIR``, never a structure of its own;
- nothing it produces claims to be verified: every provenance it writes is a ``system_default``
  or an ``llm_proposal``, never verified (a person's review turns a proposal into a revision);
- every requirement it cites is one of the requirements it was given, so the architecture is
  traceable to exactly the requirement set it was designed against.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from core.architecture_ir.errors import ElementType, Violation, ordered
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.domain.requirements.planning import PlanningInputV2

ENGINE_SOURCES = frozenset({ProvenanceSource.SYSTEM_DEFAULT, ProvenanceSource.LLM_PROPOSAL})


@dataclass(frozen=True, slots=True)
class ArchitectureProposal:
    ir: ArchitectureIR
    engine_version: str
    rationale: str  # why this design, for the person reviewing it
    requirement_set_id: uuid.UUID | None = None


class ArchitectureGenerator(Protocol):
    async def propose(
        self, planning_input: PlanningInputV2, *, requirement_set_id: uuid.UUID | None
    ) -> ArchitectureProposal: ...


def _provenances(ir: ArchitectureIR) -> Iterable[tuple[ElementType, str | None, str, Provenance | None]]:
    yield ElementType.ARCHITECTURE, None, "provenance", ir.provenance
    for node in ir.nodes:
        yield ElementType.NODE, node.id, "provenance", node.provenance
        for field, provenance in node.field_provenance.items():
            yield ElementType.NODE, node.id, f"field_provenance.{field}", provenance
    for connection in ir.connections:
        yield ElementType.CONNECTION, connection.id, "provenance", connection.provenance
        for field, provenance in connection.field_provenance.items():
            yield ElementType.CONNECTION, connection.id, f"field_provenance.{field}", provenance
    for assumption in ir.assumptions:
        yield ElementType.ASSUMPTION, assumption.id, "provenance", assumption.provenance


def check_proposal(proposal: ArchitectureProposal, planning_input: PlanningInputV2) -> tuple[Violation, ...]:
    """What makes a generator's output unacceptable (empty when it may be shown to a person)."""
    problems: list[Violation] = []
    if proposal.ir.provenance is None:
        problems.append(
            Violation(
                "missing_provenance",
                "A proposal must say it was generated (system_default or llm_proposal provenance).",
                "provenance",
                ElementType.ARCHITECTURE,
            )
        )
    for element, element_id, field, provenance in _provenances(proposal.ir):
        if provenance is None:
            continue
        if provenance.source not in ENGINE_SOURCES:
            problems.append(
                Violation(
                    "not_generated",
                    f"An engine cannot attribute a value to {provenance.source}.",
                    field,
                    element,
                    element_id,
                )
            )
        if provenance.verified:
            problems.append(
                Violation(
                    "claims_verified", "An engine's output is never verified.", field, element, element_id
                )
            )
    given = {uuid.UUID(r["id"]) for r in planning_input["requirements"]}
    cited = [
        (ElementType.ARCHITECTURE, None, proposal.ir.requirement_refs),
        *((ElementType.NODE, n.id, n.requirement_refs) for n in proposal.ir.nodes),
        *((ElementType.CONNECTION, c.id, c.requirement_refs) for c in proposal.ir.connections),
    ]
    problems += [
        Violation(
            "requirement_not_given",
            f"Requirement {ref.requirement_id} is not in the planning input.",
            "requirement_refs",
            element,
            element_id,
        )
        for element, element_id, refs in cited
        for ref in refs
        if ref.requirement_id not in given
    ]
    return ordered(problems)
