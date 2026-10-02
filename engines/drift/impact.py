"""Impact context for drift findings (rule ``drift-impact@1``): which engines' models read what a
finding concerns, and what those engines' stored analyses of the baseline revision say about the same
element — context for a person, never an impact claim. Nothing is recomputed: a capacity, cost,
reliability, security or observability consequence of the discovered state is unknown until that
engine analyzes a revision holding it.

Which engines, and why:

- a configuration or resource value: the engines the component's catalog specification lists as
  reading that property (exact, by component); for a node without a component, the engines the
  catalog lists as reading it for any component (stated as such); none named: none claimed;
- a kind, component or technology, and a component added or removed: validation, which checks
  structure and component constraints;
- a connection added, removed or modified: capacity (routing), reliability (dependencies) and
  security (connections), whose models read connections;
- a not-comparable or scope finding: none.

A finding about a baseline element also lists the requirements it references and the decisions that
name it as a subject — for review: a requirement is never marked violated, nor a decision invalid.

For each named engine, the stored analysis of the baseline's exact revision and content is
``current`` (with its own items about the element); one of another revision or content is ``stale``
— named, not to be relied on; none is ``missing``. Drift detection works without any of them.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace

from core.architecture_ir.model import ArchitectureIR
from core.domain.components.entities import Engine
from core.domain.components.repository import ComponentCatalog
from core.domain.components.specifications import ComponentSpecification
from core.domain.drift.analyses import BaselineRef
from core.domain.drift.findings import DriftFinding, ImpactRef
from core.domain.drift.values import Classification, ElementType, FindingType
from core.domain.evolution.values import EvidenceSource, EvidenceState
from core.domain.migrations.evidence import AnalysisEvidence

RULE = "drift-impact@1"
F = FindingType
ENGINES = {e: EvidenceSource(e.value) for e in Engine if e.value in {s.value for s in EvidenceSource}}
STRUCTURE = frozenset({F.COMPONENT_ADDED, F.COMPONENT_REMOVED, F.COMPONENT_MODIFIED, F.MAPPING_CHANGED})
CONNECTIONS = {
    EvidenceSource.CAPACITY: "Capacity routes traffic over connections.",
    EvidenceSource.RELIABILITY: "Reliability models dependencies as connections.",
    EvidenceSource.SECURITY: "Security reads connections (transport, trust zones).",
}
VALIDATION = "Validation checks the structure and each component's constraints."
MAX_ITEMS = 50

type Relevance = list[tuple[EvidenceSource, str]]


def _property_engines(prop: str, spec: ComponentSpecification | None, catalog: ComponentCatalog) -> Relevance:
    if spec is not None:
        field = next((f for f in spec.configuration if f.property == prop), None)
        if field is not None:
            return [
                (ENGINES[e], f"{spec.ref} lists {e.value} as reading {prop}.")
                for e in field.engines
                if e in ENGINES
            ]
    readers: set[EvidenceSource] = set()
    for other in catalog.list():
        for field in other.configuration:
            if field.property == prop:
                readers.update(ENGINES[e] for e in field.engines if e in ENGINES)
    note = "The component catalog lists {} as reading {} for some components."
    return [(engine, note.format(engine.value, prop)) for engine in sorted(readers)]


def relevance(finding: DriftFinding, ir: ArchitectureIR, catalog: ComponentCatalog) -> Relevance:
    """The engines whose models read what the finding concerns, and why — or none."""
    if finding.element is ElementType.SCOPE or finding.classification is Classification.NOT_COMPARABLE:
        return []
    if finding.element is ElementType.CONNECTION:
        return sorted(CONNECTIONS.items())
    path = finding.path or ""
    if path.startswith("configuration."):
        node = ir.node(finding.baseline_id) if finding.baseline_id else None
        component = node.component if node is not None else None
        spec = next((s for s in catalog.list() if s.id == component), None) if component else None
        return _property_engines(path.removeprefix("configuration."), spec, catalog)
    if finding.type in STRUCTURE:
        return [(EvidenceSource.VALIDATION, VALIDATION)]
    return []


def _analyses(evidence: Iterable[AnalysisEvidence]) -> dict[EvidenceSource, list[AnalysisEvidence]]:
    by_engine: dict[EvidenceSource, list[AnalysisEvidence]] = defaultdict(list)
    for item in evidence:
        by_engine[item.stored.source].append(item)
    return by_engine


def _reference(
    engine: EvidenceSource,
    basis: str,
    found: list[AnalysisEvidence],
    baseline: BaselineRef,
    element: str | None,
) -> ImpactRef:
    exact = (baseline.revision_number, baseline.content_hash)
    current = [a.stored for a in found if (a.stored.revision_number, a.stored.content_hash) == exact]
    if current:
        stored = current[0]
        items = sorted(i.item for i in stored.items if element is not None and i.element_id == element)
        return ImpactRef(
            engine, basis, EvidenceState.CURRENT, str(stored.analysis_id), stored.revision_number,
            tuple(items[:MAX_ITEMS]),
        )  # fmt: skip
    if found:
        stored = found[0].stored
        note = (
            f"{basis} Its analysis is of revision {stored.revision_number}, not the baseline: not relied on."
        )
        return ImpactRef(engine, note, EvidenceState.STALE, str(stored.analysis_id), stored.revision_number)
    return ImpactRef(engine, f"{basis} No analysis of the baseline is stored.", EvidenceState.MISSING)


def references(ir: ArchitectureIR, element_id: str | None) -> tuple[str, ...]:
    """The requirements a baseline element references and the decisions that concern it."""
    if element_id is None:
        return ()
    element = ir.node(element_id) or ir.connection(element_id)
    found: list[str] = []
    for ref in element.requirement_refs if element is not None else ():
        found.append(f"requirement:{ref.requirement_id}" + (f"@{ref.version}" if ref.version else ""))
    found += [f"decision:{d.decision_id}" for d in ir.decisions if element_id in d.subject_ids]
    return tuple(sorted(set(found)))


def contextualize(
    findings: tuple[DriftFinding, ...],
    ir: ArchitectureIR,
    baseline: BaselineRef,
    catalog: ComponentCatalog,
    evidence: tuple[AnalysisEvidence, ...] = (),
) -> tuple[DriftFinding, ...]:
    """Each finding with its impact context: the engines that read it, and their stored analyses of
    the baseline revision. The comparison result itself is unchanged."""
    by_engine = _analyses(evidence)
    found: list[DriftFinding] = []
    for finding in findings:
        impact = tuple(
            _reference(engine, basis, by_engine.get(engine, []), baseline, finding.baseline_id)
            for engine, basis in relevance(finding, ir, catalog)
        )
        referenced = references(ir, finding.baseline_id) if finding.element is not ElementType.SCOPE else ()
        found.append(
            replace(finding, impact=impact, references=referenced) if impact or referenced else finding
        )
    return tuple(found)
