"""Comparing discovery results — with each other (a re-run) and with an architecture revision (a
baseline) — stating first whether the comparison can be trusted, and never claiming drift it cannot
support.

**Two results** are compared only on what both read the same way:

- extractor and rule versions both used but different make the results **not comparable** (a change
  could be the parser's, not the source's);
- an artifact supplied to only one run, an extractor or rule only one used, or an artifact either
  run read only partly (or not at all) narrows the comparison — **partially comparable**, each
  limitation stated, and what lies outside it is not compared;
- with no artifact both read, the results are **not comparable**.

Within scope, entities are matched by key and relationships by (source, target or reference); the
differences are between the **declared** state of the two sets of sources — not runtime drift.

**A result and a baseline revision**: proposed nodes are matched to the revision's nodes by id (the
IR diff), connections by (source, kind, target) — ids of discovered connections never equal those of
hand-made ones. An element of the baseline the sources do not describe is ``not_in_sources``
(unknown — static sources need not cover everything), never "removed"; a field the sources do not
state is ``not_in_sources`` too, never "deleted". Nothing is matched by name. A baseline sharing no
node id with the proposal is not comparable.

Each comparison names both sides (result fingerprints, the revision's content hash) so a later
consumer (drift detection) can cite exactly what was compared.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from core.architecture_ir.diff import ChangeKind, FieldChange, diff
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash

from .findings import CandidateEntity, CandidateRelationship
from .results import DiscoveryResult
from .values import ArtifactStatus, fingerprint

COMPARISON_VERSION = 1
IGNORED_CATEGORIES = frozenset({"provenance", "traceability"})  # how it is known, not what it is
READ = frozenset({ArtifactStatus.PARSED, ArtifactStatus.PARTIAL})
MAX_LISTED = 10


class Comparability(StrEnum):
    COMPARABLE = "comparable"
    PARTIALLY_COMPARABLE = "partially_comparable"  # within the stated limitations only
    NOT_COMPARABLE = "not_comparable"  # no difference is reported


class Change(StrEnum):
    ADDED = "added"  # only in the later result
    REMOVED = "removed"  # only in the earlier result (of the artifacts both read)
    MODIFIED = "modified"
    ONLY_IN_SOURCES = "only_in_sources"  # discovered; not in the baseline
    NOT_IN_SOURCES = "not_in_sources"  # in the baseline; the sources do not describe it (unknown)
    DIFFERS = "differs"  # both state it, differently


@dataclass(frozen=True, slots=True)
class Limitation:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class FieldDifference:
    field: str
    before: Any  # the earlier result's or the baseline's value; None: not stated
    after: Any  # the later result's or the discovered value; None: not stated
    change: Change

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "before": self.before, "after": self.after, "change": self.change.value}


@dataclass(frozen=True, slots=True)
class Difference:
    element: str  # "entity", "relationship", "node", "connection"
    subject: str  # an entity key, a node id, a relationship or connection signature
    change: Change
    fields: tuple[FieldDifference, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "element": self.element,
            "subject": self.subject,
            "change": self.change.value,
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass(frozen=True, slots=True)
class ResultComparison:
    earlier: str  # result fingerprints
    later: str
    comparability: Comparability
    limitations: tuple[Limitation, ...] = ()
    differences: tuple[Difference, ...] = ()
    compared_artifacts: tuple[str, ...] = ()
    version: int = COMPARISON_VERSION

    @property
    def identical(self) -> bool:
        return self.earlier == self.later

    def to_dict(self) -> dict[str, Any]:
        content = {
            "version": self.version,
            "earlier": self.earlier,
            "later": self.later,
            "identical": self.identical,
            "comparability": self.comparability.value,
            "limitations": [x.to_dict() for x in self.limitations],
            "compared_artifacts": list(self.compared_artifacts),
            "differences": [d.to_dict() for d in self.differences],
        }
        return content | {"fingerprint": fingerprint(content)}


@dataclass(frozen=True, slots=True)
class BaselineComparison:
    result: str  # the result's fingerprint
    baseline_content_hash: str  # the revision's content
    proposal_content_hash: str  # the proposal compared
    comparability: Comparability
    limitations: tuple[Limitation, ...] = ()
    differences: tuple[Difference, ...] = ()
    version: int = COMPARISON_VERSION

    def to_dict(self) -> dict[str, Any]:
        content = {
            "version": self.version,
            "result": self.result,
            "baseline_content_hash": self.baseline_content_hash,
            "proposal_content_hash": self.proposal_content_hash,
            "comparability": self.comparability.value,
            "limitations": [x.to_dict() for x in self.limitations],
            "differences": [d.to_dict() for d in self.differences],
        }
        return content | {"fingerprint": fingerprint(content)}


# --- two results -----------------------------------------------------------------------------------


def _versions(earlier: Mapping[str, int], later: Mapping[str, int]) -> tuple[list[Limitation], bool]:
    found: list[Limitation] = []
    changed = sorted(k for k in earlier.keys() & later.keys() if earlier[k] != later[k])
    for name in changed:
        found.append(Limitation("versions_differ", f"{name}: version {earlier[name]}, then {later[name]}."))
    for name in sorted(earlier.keys() ^ later.keys()):
        found.append(Limitation("coverage_differs", f"{name} was used by only one of the runs."))
    return found, bool(changed)


def _scope(earlier: DiscoveryResult, later: DiscoveryResult) -> tuple[list[str], list[Limitation]]:
    before = {a.path: a for a in earlier.artifacts}
    after = {a.path: a for a in later.artifacts}
    found: list[Limitation] = []
    for path in sorted(before.keys() ^ after.keys()):
        side = "earlier" if path in before else "later"
        message = f"{path} was supplied only to the {side} run; it is not compared."
        found.append(Limitation("coverage_differs", message))
    compared: list[str] = []
    for path in sorted(before.keys() & after.keys()):
        first, then = before[path].status, after[path].status
        if first not in READ or then not in READ:
            message = f"{path} was not read by both runs ({first.value}, {then.value}); it is not compared."
            found.append(Limitation("not_read", message))
            continue
        if ArtifactStatus.PARTIAL in {first, then}:
            message = f"{path} was read only partly; its unread parts are not compared."
            found.append(Limitation("partially_read", message))
        compared.append(path)
    return compared, found


def _entity_fields(entity: CandidateEntity) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "kind": entity.kind.value if entity.kind else None,
        "mapping.status": entity.mapping.status.value,
        "mapping.component_id": entity.mapping.component_id,
        "technology": entity.technology,
        "technology_version": entity.technology_version,
    }
    for mapping in entity.configuration:
        fields[f"configuration.{mapping.property}"] = mapping.value if mapping.valid else None
    return fields


def _entity_differences(
    earlier: Iterable[CandidateEntity], later: Iterable[CandidateEntity], paths: set[str]
) -> list[Difference]:
    old = {e.key: e for e in earlier if e.location.artifact in paths}
    new = {e.key: e for e in later if e.location.artifact in paths}
    found: list[Difference] = []
    for key in sorted(old.keys() | new.keys()):
        if key not in old:
            found.append(Difference("entity", key, Change.ADDED))
        elif key not in new:
            found.append(Difference("entity", key, Change.REMOVED))
        else:
            before, after = _entity_fields(old[key]), _entity_fields(new[key])
            fields = tuple(
                FieldDifference(name, before.get(name), after.get(name), Change.MODIFIED)
                for name in sorted(before.keys() | after.keys())
                if before.get(name) != after.get(name)
            )
            if fields:
                found.append(Difference("entity", key, Change.MODIFIED, fields))
    return found


def _relationship_differences(
    earlier: Iterable[CandidateRelationship], later: Iterable[CandidateRelationship], paths: set[str]
) -> list[Difference]:
    def signatures(relationships: Iterable[CandidateRelationship]) -> set[str]:
        return {
            f"{r.source} -> {r.target or r.reference}" for r in relationships if r.location.artifact in paths
        }

    old, new = signatures(earlier), signatures(later)
    added = [Difference("relationship", s, Change.ADDED) for s in sorted(new - old)]
    return added + [Difference("relationship", s, Change.REMOVED) for s in sorted(old - new)]


def compare_results(earlier: DiscoveryResult, later: DiscoveryResult) -> ResultComparison:
    """What the later result declares differently from the earlier one — within what both read alike."""
    limitations, blocking = _versions(earlier.extractors, later.extractors)
    compared, scope = _scope(earlier, later)
    limitations += scope
    if not compared:
        limitations.append(Limitation("nothing_in_common", "No artifact was read by both runs."))
        blocking = True
    differences: list[Difference] = []
    if blocking:
        verdict = Comparability.NOT_COMPARABLE
    else:
        verdict = Comparability.PARTIALLY_COMPARABLE if limitations else Comparability.COMPARABLE
        paths = set(compared)
        differences = _entity_differences(earlier.entities, later.entities, paths)
        differences += _relationship_differences(earlier.relationships, later.relationships, paths)
    return ResultComparison(
        earlier.fingerprint,
        later.fingerprint,
        verdict,
        tuple(limitations),
        tuple(differences),
        tuple(compared),
    )


# --- a result and a baseline -----------------------------------------------------------------------


def _field(field: FieldChange) -> FieldDifference:
    if field.after is None:
        change = Change.NOT_IN_SOURCES
    elif field.before is None:
        change = Change.ONLY_IN_SOURCES
    else:
        change = Change.DIFFERS
    return FieldDifference(field.field, field.before, field.after, change)


def _node_differences(baseline: ArchitectureIR, proposed: ArchitectureIR) -> list[Difference]:
    found: list[Difference] = []
    for change in diff(replace(baseline, connections=()), replace(proposed, connections=())).nodes:
        if change.change is ChangeKind.ADDED:
            found.append(Difference("node", change.element_id, Change.ONLY_IN_SOURCES))
        elif change.change is ChangeKind.REMOVED:
            found.append(Difference("node", change.element_id, Change.NOT_IN_SOURCES))
        else:
            fields = tuple(_field(f) for f in change.fields if f.category not in IGNORED_CATEGORIES)
            if fields:
                found.append(Difference("node", change.element_id, Change.DIFFERS, fields))
    return found


def _connection_differences(baseline: ArchitectureIR, proposed: ArchitectureIR) -> list[Difference]:
    def signatures(ir: ArchitectureIR) -> set[str]:
        return {f"{c.source_id} -[{c.kind.value}]-> {c.target_id}" for c in ir.connections}

    old, new = signatures(baseline), signatures(proposed)
    only = [Difference("connection", s, Change.ONLY_IN_SOURCES) for s in sorted(new - old)]
    return only + [Difference("connection", s, Change.NOT_IN_SOURCES) for s in sorted(old - new)]


def compare_with_baseline(
    result: DiscoveryResult, proposed: ArchitectureIR, baseline: ArchitectureIR
) -> BaselineComparison:
    """How a proposal of ``result`` differs from a baseline revision's content."""
    message = "The sources declare a desired state; a difference is not proof of what runs."
    limitations = [Limitation("declared_state_only", message)]
    unread = [a.path for a in result.artifacts if a.status is not ArtifactStatus.PARSED]
    if unread:
        listed = ", ".join(unread[:MAX_LISTED])
        message = f"Not fully read: {listed}; what they hold is not compared."
        limitations.append(Limitation("not_fully_read", message))
    shared = {n.id for n in baseline.nodes} & {n.id for n in proposed.nodes}
    differences: list[Difference] = []
    if baseline.nodes and not shared:
        message = "No node of the baseline has the id of a discovered node; nothing is matched by name."
        limitations.append(Limitation("no_common_ids", message))
        verdict = Comparability.NOT_COMPARABLE
    else:
        verdict = Comparability.PARTIALLY_COMPARABLE  # static sources never establish completeness
        differences = _node_differences(baseline, proposed) + _connection_differences(baseline, proposed)
    return BaselineComparison(
        result.fingerprint,
        content_hash(baseline),
        content_hash(proposed),
        verdict,
        tuple(limitations),
        tuple(differences),
    )
