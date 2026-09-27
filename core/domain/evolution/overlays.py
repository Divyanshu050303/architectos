"""A candidate overlay: an evolution candidate applied to an in-memory copy of its exact baseline
revision — never to the canonical architecture, never as a new revision.

- The changes are applied with the simulation engine's overlay step (``configure``: the IR's own edit
  commands, all or nothing, the result checked to be a valid architecture), so there is one way to
  apply a proposed change and one architecture model. Each value set is stamped with the
  candidate's provenance: ``system_default`` (filled in by ArchitectOS), ``reference:
  candidate:<id>``, ``actor: evolution:<rule>@<version>``, inferred and never verified.
- Candidates only change the configuration of existing nodes and connections: every element keeps
  its id, and none is added or removed (so no new id is ever minted).
- An overlay that cannot be built is refused with what to act on (``InvalidCandidate``): the
  baseline is not the candidate's (``baseline_mismatch``), an element does not exist
  (``unknown_element``), a property does not apply to it (``not_applicable``), or the result breaks
  an IR rule (the rule's code, and the element and field).
- The **diff** is the IR's own structural diff from the baseline to the overlay. ``to_dict`` holds
  the candidate id, the baseline, the overlay's content hash, each change (before → after) and the
  diff — never the architecture itself; ``reconstruct`` re-applies the candidate to the stored
  revision and refuses if the result differs.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.architecture_ir.commands import InvalidArchitectureCommand
from core.architecture_ir.diff import ArchitectureDiff, diff
from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.architecture_ir.serialization import content_hash
from core.domain.simulations.overlay import AppliedChange, applied, configure
from core.domain.simulations.scenarios import Scenario, specs_of

from .candidates import Candidate
from .errors import InvalidCandidate


def provenance_of(candidate: Candidate) -> Provenance:
    return Provenance(
        ProvenanceSource.SYSTEM_DEFAULT,
        reference=f"candidate:{candidate.id}",
        inferred=True,
        actor=f"evolution:{candidate.rule.id}@{candidate.rule.version}",
    )


def _refused(candidate: Candidate, reason: str, element_id: str = "", prop: str = "") -> InvalidCandidate:
    details = {"candidate_id": candidate.id, "reason": reason}
    if element_id:
        details["element_id"] = element_id
    if prop:
        details["property"] = prop
    return InvalidCandidate(details=details)


@dataclass(frozen=True, slots=True)
class CandidateOverlay:
    candidate: Candidate
    baseline: ArchitectureIR  # the exact revision, unchanged
    architecture: ArchitectureIR  # the candidate's in-memory copy
    changes: tuple[AppliedChange, ...]

    @property
    def diff(self) -> ArchitectureDiff:
        """The IR's structural diff from the baseline (computed when asked: only a candidate's detail
        needs it)."""
        return diff(self.baseline, self.architecture)

    @property
    def content_hash(self) -> str:
        return content_hash(self.architecture)

    def scenario(self) -> Scenario:
        """The candidate as a simulation scenario (its changes only), to evaluate its impact."""
        return Scenario(self.candidate.id, changes=self.candidate.changes)

    def to_dict(self) -> dict[str, Any]:
        """Everything needed to explain and reconstruct the overlay — never the architecture."""
        return {
            "candidate_id": self.candidate.id,
            "baseline": self.candidate.baseline.to_dict(),
            "content_hash": self.content_hash,
            "changes": [
                {"element_id": c.element_id, "property": c.property, "change": c.evidence().value}
                for c in self.changes
            ],
            "summary": self.diff.summary(),
            "diff": self.diff.to_dict(),
        }


def apply_candidate(ir: ArchitectureIR, candidate: Candidate) -> CandidateOverlay:
    """``candidate`` applied to an in-memory copy of ``ir``, which must be its exact baseline."""
    if content_hash(ir) != candidate.baseline.content_hash:
        raise _refused(candidate, "baseline_mismatch")
    for change in candidate.changes:
        node = ir.node(change.element_id)
        connection = ir.connection(change.element_id) if node is None else None
        if node is None and connection is None:
            raise _refused(candidate, "unknown_element", change.element_id, change.property)
        kind = node.kind if node is not None else "connection"
        if not any(kind in spec.applies_to for spec in specs_of(change.property)):
            raise _refused(candidate, "not_applicable", change.element_id, change.property)
    try:
        architecture = configure(ir, candidate.changes, provenance_of(candidate))
    except InvalidArchitectureCommand as error:
        reason = str(error.details.get("reason") or "not_applicable")
        raise _refused(candidate, reason, str(error.details.get("element_id") or "")) from None
    except InvalidArchitecture as error:  # e.g. more minimum healthy replicas than replicas
        violation = (error.details.get("violations") or [{}])[0]
        raise _refused(
            candidate,
            str(violation.get("rule") or "invalid_architecture"),
            str(violation.get("element_id") or ""),
            str(violation.get("field") or ""),
        ) from None
    same = [n.id for n in ir.nodes] == [n.id for n in architecture.nodes] and [
        c.id for c in ir.connections
    ] == [c.id for c in architecture.connections]
    if not same:  # configuration commands never add, remove or rename: an IR bug if they did
        raise _refused(candidate, "elements_changed")
    return CandidateOverlay(candidate, ir, architecture, applied(ir, architecture, candidate.changes))


def reconstruct(ir: ArchitectureIR, candidate: Candidate, stored: Mapping[str, Any]) -> CandidateOverlay:
    """The overlay of a stored candidate, re-applied to its revision; refused when it no longer gives
    what was stored."""
    overlay = apply_candidate(ir, candidate)
    if dict(stored) != overlay.to_dict():
        raise _refused(candidate, "not_reproducible")
    return overlay
