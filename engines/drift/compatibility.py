"""Whether a baseline revision and a discovery run can be compared — decided before any difference is
computed, dimension by dimension (rule ``drift-compatibility@1``), so a parser or schema change is
never read as an architecture change and missing coverage never as a removal.

- ``ir_schema``: the revision in the current IR schema; an older one is compared upgraded (warning),
  a newer one cannot be compared.
- ``discovery_result``: the run's result in the version this engine reads; otherwise incompatible.
- ``extractor_versions``: the same extractors and rules as the run the baseline was accepted from. A
  changed shared rule (mapping, configuration, relationships, proposal) makes the inputs incompatible;
  a changed extractor or kind rule makes that source type not comparable; a baseline not accepted from
  a discovery rests on unknown versions (warning).
- ``source_types``: the run reads the source types the baseline's discovered elements came from;
  some not read: partially comparable; none shared: incompatible; nothing discovered: warning.
- ``source_coverage``: every artifact read completely, nothing unsupported or unresolved; partly read
  or unread artifacts: partially comparable; nothing read: incompatible; unsupported or unresolved
  items: warning.
- ``artifact_coverage``: every artifact a baseline element was discovered from read completely;
  otherwise partially comparable — those elements' absence is not removal.
- ``identity``: baseline nodes share identifiers (or confirmed mappings) with discovered entities;
  none does: cannot be determined — confirm identity mappings.
- ``freshness``: the baseline is the current revision and the run not older than it; otherwise a
  warning — the comparison is exact, but may be out of date.

The overall outcome is the least comparable dimension.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.versioning import IR_SCHEMA_VERSION
from core.domain.discovery.results import RESULT_VERSION, DiscoveryResult
from core.domain.discovery.values import ArtifactStatus, SourceType
from core.domain.drift.analyses import BaselineRef, Coverage, ObservedRef
from core.domain.drift.findings import CompatibilityCheck
from core.domain.drift.values import Compatibility, worst

from .sources import DiscoveredFrom, coverage_of, discovered_from

RULE = "drift-compatibility@1"
C = Compatibility
MAX_LISTED = 10
# Discovery rules every source type depends on, and each source type's own kind rule.
SHARED_RULES = frozenset(
    {"discovery-catalog", "discovery-configuration", "discovery-relationships", "discovery-proposal"}
)
SOURCE_RULES = {
    "kubernetes-kinds": SourceType.KUBERNETES,
    "compose-kinds": SourceType.DOCKER_COMPOSE,
    "terraform-kinds": SourceType.TERRAFORM_JSON,
    "architecture-json": SourceType.ARCHITECTURE_JSON,
}


@dataclass(frozen=True)
class BaselineInput:
    ref: BaselineRef
    ir: ArchitectureIR
    created_at: datetime
    latest_revision: int  # the architecture's current revision
    source: DiscoveryResult | None = None  # the run the baseline was accepted from, if it was


@dataclass(frozen=True)
class ObservedInput:
    ref: ObservedRef
    result: DiscoveryResult
    requested_at: datetime


@dataclass(frozen=True)
class Assessment:
    checks: tuple[CompatibilityCheck, ...]
    coverage: Coverage
    warnings: tuple[str, ...] = ()
    not_comparable: frozenset[SourceType] = field(default_factory=frozenset)  # their parsers changed
    uninspected: frozenset[str] = frozenset()  # baseline artifacts the run did not read completely

    @property
    def status(self) -> Compatibility:
        return worst(c.outcome for c in self.checks)


def _listed(values: list[str]) -> str:
    shown = ", ".join(values[:MAX_LISTED])
    return shown + (f" (and {len(values) - MAX_LISTED} more)" if len(values) > MAX_LISTED else "")


def _check(dimension: str, outcome: Compatibility, message: str) -> CompatibilityCheck:
    return CompatibilityCheck(dimension, outcome, message)


def _schema(baseline: BaselineInput) -> CompatibilityCheck:
    version = baseline.ref.schema_version
    if version == IR_SCHEMA_VERSION:
        return _check("ir_schema", C.COMPATIBLE, f"The revision is in IR schema {version}.")
    if version < IR_SCHEMA_VERSION:
        message = (
            f"The revision was stored in IR schema {version}; it is compared upgraded to {IR_SCHEMA_VERSION}."
        )
        return _check("ir_schema", C.COMPATIBLE_WITH_WARNINGS, message)
    message = f"The revision is in IR schema {version}, newer than this engine's {IR_SCHEMA_VERSION}."
    return _check("ir_schema", C.INCOMPATIBLE, message)


def _result_version(observed: ObservedInput) -> CompatibilityCheck:
    version = observed.result.version
    if version == RESULT_VERSION:
        return _check("discovery_result", C.COMPATIBLE, f"Discovery result version {version}.")
    message = f"Discovery result version {version} is not the version {RESULT_VERSION} this engine reads."
    return _check("discovery_result", C.INCOMPATIBLE, message)


def _affected(changed: list[str]) -> set[SourceType]:
    affected: set[SourceType] = set()
    for name in changed:
        if name in SOURCE_RULES:
            affected.add(SOURCE_RULES[name])
        elif name in set(SourceType):
            affected.add(SourceType(name))
    return affected


def _versions(
    baseline: BaselineInput, observed: ObservedInput, read: frozenset[SourceType]
) -> tuple[CompatibilityCheck, frozenset[SourceType]]:
    if baseline.source is None:
        message = (
            "The baseline was not accepted from a discovery: the parser versions it rests on are unknown."
        )
        return _check("extractor_versions", C.COMPATIBLE_WITH_WARNINGS, message), frozenset()
    before, after = baseline.source.extractors, observed.result.extractors
    changed = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
    if not changed:
        return _check(
            "extractor_versions", C.COMPATIBLE, "The same extractor and rule versions."
        ), frozenset()
    described = _listed([f"{k} {before[k]} -> {after[k]}" for k in changed])
    if SHARED_RULES & set(changed):
        message = f"Shared discovery rules changed ({described}): a difference could be the rules'."
        return _check("extractor_versions", C.INCOMPATIBLE, message), read
    affected = _affected(changed)
    outcome = C.INCOMPATIBLE if read and read <= affected else C.PARTIALLY_COMPARABLE
    sources = ", ".join(sorted(s.value for s in affected))
    message = f"Extractor or kind rules changed ({described}): {sources} is not compared."
    return _check("extractor_versions", outcome, message), frozenset(affected)


def _source_types(
    discovered: Mapping[str, DiscoveredFrom], read: frozenset[SourceType]
) -> CompatibilityCheck:
    if not discovered:
        message = "No baseline element was discovered from artifacts: removals cannot be established."
        return _check("source_types", C.COMPATIBLE_WITH_WARNINGS, message)
    wanted = {d.source_type for d in discovered.values()}
    if not wanted & read:
        names = ", ".join(sorted(s.value for s in wanted))
        return _check(
            "source_types", C.INCOMPATIBLE, f"The run reads none of the baseline's source types ({names})."
        )
    missing = sorted(s.value for s in wanted - read)
    if missing:
        message = (
            f"Not read by the run: {', '.join(missing)}; the elements discovered from them are not compared."
        )
        return _check("source_types", C.PARTIALLY_COMPARABLE, message)
    return _check("source_types", C.COMPATIBLE, "The run reads the baseline's source types.")


def _source_coverage(coverage: Coverage) -> CompatibilityCheck:
    if not coverage.inspected and not coverage.partial:
        return _check("source_coverage", C.INCOMPATIBLE, "The run read no artifact.")
    if coverage.partial or coverage.unread:
        message = f"Not read completely: {_listed([*coverage.partial, *coverage.unread])}."
        return _check("source_coverage", C.PARTIALLY_COMPARABLE, message)
    if not coverage.complete:
        message = (
            f"{coverage.unsupported_constructs} unsupported construct(s), {coverage.unresolved_entities} "
            f"unresolved mapping(s), {coverage.unresolved_relationships} unresolved reference(s)."
        )
        return _check("source_coverage", C.COMPATIBLE_WITH_WARNINGS, message)
    return _check("source_coverage", C.COMPATIBLE, "Every artifact was read completely.")


def _artifact_coverage(
    discovered: Mapping[str, DiscoveredFrom], observed: DiscoveryResult
) -> tuple[CompatibilityCheck, frozenset[str]]:
    complete = {a.path for a in observed.artifacts if a.status is ArtifactStatus.PARSED}
    uninspected = frozenset(d.artifact for d in discovered.values() if d.artifact not in complete)
    if not discovered:
        return _check(
            "artifact_coverage", C.COMPATIBLE, "No baseline element names an artifact."
        ), uninspected
    if uninspected:
        message = (
            f"Not read completely by the run: {_listed(sorted(uninspected))}; absence there is not removal."
        )
        return _check("artifact_coverage", C.PARTIALLY_COMPARABLE, message), uninspected
    message = "Every artifact a baseline element was discovered from was read completely."
    return _check("artifact_coverage", C.COMPATIBLE, message), uninspected


def _identity(
    ir: ArchitectureIR, observed: DiscoveryResult, mappings: Mapping[str, str]
) -> CompatibilityCheck:
    keys = {e.key for e in observed.entities}
    nodes = {n.id for n in ir.nodes}
    shared = {n for n in nodes if n in keys or mappings.get(n) in keys}
    if nodes and not shared:
        message = "No baseline node shares an identifier with a discovered entity: confirm identity mappings."
        return _check("identity", C.CANNOT_DETERMINE, message)
    message = f"{len(shared)} of {len(nodes)} baseline nodes have a stable identity match."
    return _check("identity", C.COMPATIBLE, message)


def _freshness(baseline: BaselineInput, observed: ObservedInput) -> CompatibilityCheck:
    notes: list[str] = []
    revision = baseline.ref.revision_number
    if baseline.latest_revision > revision:
        notes.append(f"Revision {baseline.latest_revision} is later than the baseline revision {revision}.")
    if observed.requested_at < baseline.created_at:
        notes.append("The discovery run is older than the baseline revision.")
    if notes:
        return _check("freshness", C.COMPATIBLE_WITH_WARNINGS, " ".join(notes))
    return _check("freshness", C.COMPATIBLE, "The current revision, and a run not older than it.")


def assess(
    baseline: BaselineInput, observed: ObservedInput, mappings: Mapping[str, str] | None = None
) -> Assessment:
    """Every compatibility dimension, the run's coverage and what cannot be compared."""
    coverage = coverage_of(observed.result)
    read = frozenset(
        a.source_type
        for a in observed.result.artifacts
        if a.source_type is not None and a.status in {ArtifactStatus.PARSED, ArtifactStatus.PARTIAL}
    )
    elements: list[Node | Connection] = [*baseline.ir.nodes, *baseline.ir.connections]
    discovered = {e.id: found for e in elements if (found := discovered_from(e)) is not None}
    versions, not_comparable = _versions(baseline, observed, read)
    artifacts, uninspected = _artifact_coverage(discovered, observed.result)
    checks = (
        _schema(baseline),
        _result_version(observed),
        versions,
        _source_types(discovered, read),
        _source_coverage(coverage),
        artifacts,
        _identity(baseline.ir, observed.result, mappings or {}),
        _freshness(baseline, observed),
    )
    warnings = tuple(c.message for c in checks if c.outcome is not C.COMPATIBLE)
    return Assessment(checks, coverage, warnings, not_comparable, uninspected)
