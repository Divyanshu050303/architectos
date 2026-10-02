"""Where things come from: a baseline element's discovery source (format and artifact, from the
provenance discovery recorded on it), and what a discovery run inspected (its coverage).

A node or connection a person accepted from a discovery run carries provenance ``actor`` =
``discovery:<source type>`` and ``reference`` = ``<artifact path>#<document>:<pointer>`` (artifact
paths hold neither ``#`` nor ``:``). An element written by a person, or imported otherwise, has no
discovery source: whether it still exists cannot be established from artifacts alone.
"""

from dataclasses import dataclass

from core.architecture_ir.edge import Connection
from core.architecture_ir.node import Node
from core.domain.discovery.results import CONFIDENT, DiscoveryResult
from core.domain.discovery.values import (
    ArtifactStatus,
    EntityRole,
    FindingType,
    RelationshipStatus,
    Severity,
    SourceType,
)
from core.domain.drift.analyses import Coverage

ACTOR = "discovery:"


@dataclass(frozen=True, slots=True)
class DiscoveredFrom:
    source_type: SourceType
    artifact: str  # the artifact path, as supplied to the run
    reference: str  # "path#document:pointer"


def discovered_from(element: Node | Connection) -> DiscoveredFrom | None:
    """The discovery source recorded on an element's provenance — None when it was not discovered."""
    provenance = element.provenance
    actor = provenance.actor if provenance is not None else None
    if provenance is None or provenance.reference is None or not actor or not actor.startswith(ACTOR):
        return None
    try:
        source = SourceType(actor.removeprefix(ACTOR))
    except ValueError:
        return None
    artifact = provenance.reference.split("#", 1)[0].split(":", 1)[0]
    return DiscoveredFrom(source, artifact, provenance.reference) if artifact else None


def coverage_of(result: DiscoveryResult) -> Coverage:
    """What the run inspected: artifacts by status, and what it could not interpret or resolve."""
    by_status: dict[ArtifactStatus, list[str]] = {status: [] for status in ArtifactStatus}
    for artifact in result.artifacts:
        by_status[artifact.status].append(artifact.path)
    components = [e for e in result.entities if e.role is EntityRole.COMPONENT]
    return Coverage(
        source_types=tuple(sorted({a.source_type.value for a in result.artifacts if a.source_type})),
        inspected=tuple(by_status[ArtifactStatus.PARSED]),
        partial=tuple(by_status[ArtifactStatus.PARTIAL]),
        unread=tuple(by_status[ArtifactStatus.UNSUPPORTED] + by_status[ArtifactStatus.FAILED]),
        unsupported_constructs=sum(1 for f in result.findings if f.type is FindingType.UNSUPPORTED),
        unresolved_entities=sum(1 for e in components if e.mapping.status not in CONFIDENT),
        unresolved_relationships=sum(
            1 for r in result.relationships if r.status is RelationshipStatus.UNRESOLVED
        ),
        errors=sum(1 for d in result.diagnostics if d.severity is Severity.ERROR),
    )
