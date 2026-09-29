"""The dependency graph of a plan's steps: which steps each one waits for, read from the steps as
they are.

A dependency on a step the plan does not contain is reported, never dropped silently; so is a
cycle — every strongly connected group of steps, found deterministically. Only a graph with
neither can be ordered: its order is a topological order with ties broken by step key, so the same
steps always give the same order.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from core.domain.migrations.plans import PlanFinding
from core.domain.migrations.steps import MigrationStep
from core.domain.migrations.values import FindingType


@dataclass(frozen=True)
class DependencyGraph:
    steps: dict[str, MigrationStep]  # by id

    @classmethod
    def of(cls, steps: Iterable[MigrationStep]) -> DependencyGraph:
        return cls({s.id: s for s in sorted(steps, key=lambda s: s.key)})

    def key(self, step_id: str) -> str:
        return self.steps[step_id].key

    def unknown(self) -> dict[str, tuple[str, ...]]:
        """The dependencies naming a step the plan does not contain, by the step that names them."""
        found = {i: tuple(d for d in s.depends_on if d not in self.steps) for i, s in self.steps.items()}
        return {i: missing for i, missing in found.items() if missing}

    def _edges(self, step_id: str) -> tuple[str, ...]:
        """The known steps ``step_id`` waits for, by key."""
        known = (d for d in self.steps[step_id].depends_on if d in self.steps)
        return tuple(sorted(known, key=self.key))

    def cycles(self) -> tuple[tuple[str, ...], ...]:
        """Every group of steps that wait for each other (Tarjan's strongly connected components,
        iteratively), each by key, in key order."""
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        stack: list[str] = []
        on_stack: set[str] = set()
        groups: list[tuple[str, ...]] = []

        def enter(node: str) -> None:
            index[node] = low[node] = len(index)
            stack.append(node)
            on_stack.add(node)

        for root in self.steps:  # already in key order
            if root in index:
                continue
            enter(root)
            work = [(root, iter(self._edges(root)))]
            while work:
                node, edges = work[-1]
                nxt = next((e for e in edges if e not in index or e in on_stack), None)
                if nxt is not None:
                    if nxt in index:
                        low[node] = min(low[node], index[nxt])
                    else:
                        enter(nxt)
                        work.append((nxt, iter(self._edges(nxt))))
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    group: list[str] = []
                    while not group or group[-1] != node:
                        group.append(stack.pop())
                        on_stack.discard(group[-1])
                    if len(group) > 1:
                        groups.append(tuple(sorted(group, key=self.key)))
        return tuple(sorted(groups, key=lambda g: self.key(g[0])))

    def ancestors(self, step_id: str) -> frozenset[str]:
        """Every step ``step_id`` waits for, directly or through others."""
        seen: set[str] = set()
        todo = list(self._edges(step_id))
        while todo:
            current = todo.pop()
            if current not in seen:
                seen.add(current)
                todo.extend(self._edges(current))
        return frozenset(seen)

    def order(self) -> tuple[str, ...]:
        """A topological order (Kahn's), ties broken by step key; steps in a cycle are left out."""
        waiting = {i: set(self._edges(i)) for i in self.steps}
        ready = sorted((i for i, deps in waiting.items() if not deps), key=self.key)
        ordered: list[str] = []
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for i, deps in waiting.items():
                if current in deps:
                    deps.discard(current)
                    if not deps:
                        ready.append(i)
            ready.sort(key=self.key)
        return tuple(ordered)

    def levels(self) -> tuple[tuple[str, ...], ...]:
        """The steps by level — each after every step it waits for — each level in key order.
        Only for a graph without unknown dependencies or cycles."""
        if self.unknown() or self.cycles():
            raise ValueError("only a valid dependency graph can be ordered")
        level: dict[str, int] = {}
        for step in self.order():
            level[step] = 1 + max((level[d] for d in self._edges(step)), default=-1)
        count = 1 + max(level.values(), default=-1)
        return tuple(tuple(sorted((s for s in level if level[s] == n), key=self.key)) for n in range(count))

    def findings(self) -> tuple[PlanFinding, ...]:
        """Dependencies on steps that do not exist, and cycles — each stated with its steps."""
        found = [
            PlanFinding(
                FindingType.INVALID_DEPENDENCY,
                self.key(i),
                f"{self.key(i)} depends on {len(missing)} step(s) the plan does not contain.",
                step_ids=(i,),
                missing=tuple(f"The step {m}, or no dependency on it." for m in missing),
            )
            for i, missing in sorted(self.unknown().items(), key=lambda item: self.key(item[0]))
        ]
        for group in self.cycles():
            keys = [self.key(i) for i in group]
            found.append(
                PlanFinding(
                    FindingType.DEPENDENCY_CYCLE,
                    keys[0],
                    f"These steps wait for each other, so none can be carried out first: {', '.join(keys)}.",
                    element_ids=tuple(e for i in group for e in self.steps[i].element_ids),
                    step_ids=group,
                    missing=("A dependency removed so that one of them can be carried out first.",),
                )
            )
        return tuple(found)
