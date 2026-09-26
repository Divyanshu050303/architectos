"""End-to-end availability: how component failures propagate to a request path.

A path (``dependency.py``) is everything an entry's requests require. Its availability composes only
what the architecture makes explicit:

- **Series**: every required component must be available: the product of their availabilities.
- **Declared alternatives**: members of one redundancy group that the path reaches are alternatives.
  Each member's **branch** is the member and what only it requires (not what the path reaches
  without the group, nor what every branch requires: those stay in series). The group is available
  when at least ``k`` (``redundancy_group_min_healthy``) branches are:
  ``P(at least k of n)``, exactly, for branches of different availability. Only when every member
  declares ``failure_independence: independent`` and ``failover_mode: automatic`` (declared, not
  verified) and they agree on ``k``.

The estimate is known only when every input is; otherwise it names what is missing. Not composed
(the path stays unknown and says so): branches that overlap only in part, a group nested in another
group's branch, members that disagree. A group with one member on the path is in series (its other
members are not routed: a topology finding says so). Every estimate states its assumptions: failures
of different components are independent, failover time is not deducted.

Results are per entry: there is no architecture-wide availability, which would need semantics (the
share of requests per entry) the architecture does not declare.
"""

from collections import defaultdict
from decimal import Decimal

from core.domain.capacity.results import MAX_ITEMS, Estimate, Source
from core.domain.capacity.units import rounded_text
from core.domain.engine_results import Evidence, Unsupported
from core.domain.numbers import arithmetic
from core.domain.reliability.results import PathResult
from core.domain.reliability.values import availability

from .context import ReliabilityContext
from .dependency import Closure, closure_of, entries
from .engine import ARCHITECTURE, Progress, StepMeta, StepOutput, names

LISTED = 150
SHOWN = 100  # values an evidence line lists before "and N more" (the components hold them all)
BASIS = (
    "series: product of every required component's availability; declared alternatives (redundancy groups): "
    "P(at least k of n branches)"
)
INDEPENDENCE = "Failures of different components are independent (the series product assumes it)."
FAILOVER = "Alternatives fail over automatically and instantly (declared, not verified)."


def at_least(k: int, probabilities: list[Decimal]) -> Decimal:
    """P(at least ``k`` of these independent events), exactly."""
    exactly = [Decimal(1)]  # exactly[j]: P(exactly j so far)
    with arithmetic():
        for p in probabilities:
            nxt = [Decimal(0)] * (len(exactly) + 1)
            for j, q in enumerate(exactly):
                nxt[j] += q * (1 - p)
                nxt[j + 1] += q * p
            exactly = nxt
        return sum(exactly[k:], Decimal(0))


def _product(values: list[Decimal]) -> Decimal:
    total = Decimal(1)
    with arithmetic():
        for value in values:
            total *= value
    return total


class Composition:
    """One path's availability, built from its components and declared alternatives."""

    def __init__(self, context: ReliabilityContext, progress: Progress, found: Closure) -> None:
        self.context, self.progress, self.found = context, progress, found
        self.required = found.components(context.topology)
        self.missing: set[str] = set()
        self.evidence: list[Evidence] = [Evidence("assumption", INDEPENDENCE)]

    def _lookup(self, node_id: str) -> Decimal | None:
        component = self.progress.component(node_id)
        estimate = component.estimate("availability") if component else None
        return estimate.quantity.value if estimate is not None and estimate.quantity is not None else None

    def value(self, node_id: str) -> Decimal | None:
        value = self._lookup(node_id)
        if value is None:
            self.missing.add(f"{node_id}.availability")
        else:
            self.evidence.append(Evidence(f"{node_id}.availability", rounded_text(value)))
        return value

    def groups(self) -> dict[str, list[str]]:
        found: dict[str, list[str]] = defaultdict(list)
        for node_id in self.required:
            group = self.context.facts[node_id].known("redundancy_group")
            if isinstance(group, str):
                found[group].append(node_id)
        return {g: members for g, members in sorted(found.items()) if len(members) > 1}

    def compose(self) -> Decimal | None:
        in_series = set(self.required)
        groups = self.groups()
        members_of = {m for members in groups.values() for m in members}
        factors: list[Decimal] = []
        for group, members in groups.items():
            branches = self._branches(group, members, members_of)
            if branches is None:
                continue
            in_series -= set().union(*branches.values())
            value = self._group(group, members, branches)
            if value is not None:
                factors.append(value)
        for node_id in self.required:  # path order: deterministic evidence
            if node_id in in_series and (value := self.value(node_id)) is not None:
                factors.append(value)
        if self.missing or not self.required:
            return None
        return _product(factors)

    def _branches(self, group: str, members: list[str], members_of: set[str]) -> dict[str, set[str]] | None:
        """Each member's exclusive branch, or None (and what is missing) when they cannot be told apart."""
        required = set(self.required)
        without = set(closure_of(self.context, self.found.entry, avoid=frozenset(members)).node_ids)
        reach = {m: set(closure_of(self.context, m).node_ids) & required for m in members}
        common = set.intersection(*reach.values())
        exclusive = {m: reach[m] - without - common | {m} for m in members}
        if sum(len(b) for b in exclusive.values()) != len(set().union(*exclusive.values())):
            self.missing.add(f"disjoint.redundancy_group.{group}")
            return None
        others = members_of - set(members)
        if any(branch & others for branch in exclusive.values()):
            self.missing.add(f"unnested.redundancy_group.{group}")
            return None
        return exclusive

    def _group(self, group: str, members: list[str], branches: dict[str, set[str]]) -> Decimal | None:
        """The group's availability; computed once per analysis for the same members and branches
        (many entries often reach the same group)."""
        key = ("group", group, tuple(members), tuple(frozenset(branches[m]) for m in members))
        memo = self.context.memo
        if key not in memo:
            memo[key] = self._compose_group(group, members, branches)
        composed: tuple[Decimal | None, tuple[str, ...], tuple[Evidence, ...]] = memo[key]
        value, missing, evidence = composed
        self.missing.update(missing)
        self.evidence.extend(evidence)
        return value

    def _compose_group(
        self, group: str, members: list[str], branches: dict[str, set[str]]
    ) -> tuple[Decimal | None, tuple[str, ...], tuple[Evidence, ...]]:
        facts = [self.context.facts[m] for m in members]
        problems: list[str] = []
        for member, fact in zip(members, facts, strict=True):
            if fact.known("failure_independence") != "independent":
                problems.append(f"{member}.failure_independence")
            if fact.known("failover_mode") != "automatic":
                problems.append(f"{member}.failover_mode")
        minimums = {f.known("redundancy_group_min_healthy") for f in facts}
        k = next(iter(minimums)) if len(minimums) == 1 else None
        if not isinstance(k, int) or k > len(members):
            problems.append(f"consistent.redundancy_group.{group}.min_healthy")
        if problems or not isinstance(k, int):
            return None, tuple(problems), ()
        order = {n: i for i, n in enumerate(self.required)}
        missing: list[str] = []
        evidence: list[Evidence] = []
        values: list[Decimal] = []
        for member in members:
            parts: list[Decimal] = []
            for node_id in sorted(branches[member], key=order.__getitem__):
                part = self._lookup(node_id)
                if part is None:
                    missing.append(f"{node_id}.availability")
                else:
                    parts.append(part)
                    evidence.append(Evidence(f"{node_id}.availability", rounded_text(part)))
            if len(parts) == len(branches[member]):
                values.append(_product(parts))
        if len(values) != len(members):
            return None, tuple(missing), tuple(evidence)
        value = at_least(k, values)
        shown = [f"{m} ({rounded_text(v)})" for m, v in zip(members, values, strict=True)]
        evidence.append(
            Evidence(f"redundancy_group.{group}", f"{k} of {len(members)}: {names(shown, SHOWN)}")
        )
        evidence.append(Evidence("assumption", FAILOVER))
        return value, tuple(missing), tuple(evidence)


def _compact(evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    """At most ``LISTED`` component values one by one; beyond, they are folded into one ``series``
    line (every value still shown), so a path through hundreds of components stays within an
    estimate's bounds."""
    values = [e for e in evidence if e.label.endswith(".availability")]
    if len(values) <= LISTED:
        return evidence
    others = tuple(e for e in evidence if not e.label.endswith(".availability"))
    folded = names((f"{e.label.removesuffix('.availability')} {e.value}" for e in values), SHOWN)
    return (*others, Evidence("series", folded))


def _bounded(missing: tuple[str, ...]) -> tuple[str, ...]:
    """An estimate names at most ``MAX_ITEMS`` missing inputs: beyond, the count of the rest (the
    path's ``availability_not_evaluable`` finding lists them all)."""
    if len(missing) <= MAX_ITEMS:
        return missing
    return (*missing[: MAX_ITEMS - 1], f"more.{len(missing) - MAX_ITEMS + 1}_inputs")


def compose(context: ReliabilityContext, progress: Progress, found: Closure, meta: StepMeta) -> Estimate:
    composition = Composition(context, progress, found)
    value = composition.compose()
    evidence = _compact(tuple(dict.fromkeys(composition.evidence)))  # once each, in order
    if value is None:
        missing = _bounded(tuple(sorted(composition.missing)) or ("components",))
        return Estimate(
            found.entry, "availability", None, Source.UNKNOWN, BASIS, inputs=evidence, missing=missing
        )
    return Estimate(
        found.entry,
        "availability",
        availability(value),
        Source.MODEL_ESTIMATE,
        BASIS,
        meta.id,
        meta.version,
        evidence,
    )


class RequestPaths:
    meta = StepMeta(
        id="request-paths",
        version=2,
        name="Request paths",
        description="What each entry's requests need, and its availability (series, declared alternatives).",
        produces=("paths",),
        assumptions=(
            INDEPENDENCE,
            FAILOVER,
            "A request or data access whose interaction is not stated waits for its target.",
        ),
        limitations=(
            "Asynchronous flows (publish, consume) are recorded, not composed: a broker decouples them.",
            "Alternatives are composed only where a redundancy group declares them, with disjoint branches.",
            "Per entry only: there is no architecture-wide availability.",
        ),
    )

    def run(self, context: ReliabilityContext, progress: Progress) -> StepOutput:
        starts = entries(context)
        if not starts:
            message = "No entry: the architecture has no client, and the request names no entry."
            return StepOutput(unsupported=(Unsupported(ARCHITECTURE, "no_entry", message),))
        paths = []
        for entry in starts:
            found = closure_of(context, entry)
            estimate = compose(context, progress, found, self.meta)
            paths.append(PathResult(entry, found.node_ids, found.required, estimate, found.optional))
        return StepOutput(paths=tuple(paths))
