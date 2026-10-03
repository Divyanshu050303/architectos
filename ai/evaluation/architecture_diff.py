"""Measures the architecture diff on a small, versioned set of architecture pairs, with recorded model
outputs for the explanations.

    python -m ai.evaluation.architecture_diff             # the report, as text
    python -m ai.evaluation.architecture_diff --json      # the metrics, as JSON
    python -m ai.evaluation.architecture_diff --check     # exit 1 below the recorded thresholds

Each scenario (``datasets/architecture_diff/v1/scenarios.jsonl``) is a base architecture and the edits
that make the target, the requirements and ADRs in force, the person's context, the passages retrieval
would return, and what the model answered (recorded outputs or failures) — run through the real
comparison (semantic diff, the six engines on both states, traceability), the stored record, and the
real interpretation (bounded context, the explanation agent's checks and retry). Measured:

- ``change_accuracy``: scenarios whose changes are exactly the expected ones (element, id, kind);
- ``classification_accuracy``: expected classes present on the expected changes;
- ``grouping_accuracy``: scenarios whose groups partition the changes exactly as expected;
- ``impact_accuracy``: expected requirement relations, ADRs to review and introduced findings found;
- ``explanation_accuracy``: explanations ending as expected (status, failure, model calls);
- ``rejection_accuracy``: explanations expected to be refused, refused for the expected reasons;
- ``grounding_integrity``: completed explanations citing only what their context listed;
- ``injection_containment``: model calls whose instructions are exactly the versioned prompt and whose
  data sections all close;
- ``record_integrity``: diffs and explanation runs reading back from their stored documents unchanged;
- ``secret_leaks`` (a ceiling): secret values found in a diff, a stored record or a model request;
- ``unsupported_explanations`` (a ceiling): explanations completed where the scenario expects none;
- ``score_language`` (a ceiling): completed explanations with score, rating or winner language;
- ``calls_over_budget`` (a ceiling): explanations calling the model more than twice;
- ``stored_leaks`` (a ceiling): stored explanation runs containing the prompt or retrieved text.

**What this does not show.** The comparison is deterministic and measured as such. The explanations
are recorded answers: they measure what the diff does with an answer — accept it, refuse it, retry —
never how good a live model's explanations are. That needs a live model and reviewers, and is not
claimed here. Deterministic: the same code always scores the same.
"""

import argparse
import asyncio
import json
import re
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai.agents.diff_agent import SCORE, SYSTEM_PROMPT, DiffExplanationAgent
from ai.evaluation.architecture_agent import AT, PROJECT, USER, RecordedLlm, _passages, _requirement
from ai.llm.client import StructuredRequest
from ai.llm.guard import DATA_TAG
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash, from_dict
from core.domain.architecture_diff.diffs import ArchitectureDiff
from core.domain.architecture_diff.explanations import ExplanationRun
from core.domain.architecture_diff.ports import DiffInputs, ExplanationBudget, ImpactInputs, ResolvedState
from core.domain.architecture_diff.records import (
    diff_document,
    diff_from,
    explanation_document,
    explanation_run_from,
)
from core.domain.architecture_diff.references import ComparedState, DiffRequest, StateRef
from core.domain.architecture_diff.values import FindingState, ImpactStatus
from core.domain.decisions.entities import Decision, DecisionStatus
from core.domain.knowledge.retrieval import Passage, RetrievalQuery, RetrievalResult
from core.domain.requirements.entities import Requirement
from engines.architecture_diff.engine import DeterministicDiffEngine
from engines.architecture_diff.explanation_context import assemble, merged
from engines.architecture_diff.impact import DiffEngines
from engines.architecture_diff.interpreter import DiffInterpretation
from engines.capacity.service import DeterministicCapacityEngine
from engines.cost.service import DeterministicCostEngine
from engines.observability.service import DeterministicObservabilityEngine
from engines.reliability.service import DeterministicReliabilityEngine
from engines.security.service import DeterministicSecurityEngine
from engines.validation.service import DeterministicValidationEngine
from persistence.component_catalog import default_catalog

DATASET = Path(__file__).parent / "datasets" / "architecture_diff" / "v1"
THRESHOLDS = DATASET / "thresholds.json"
CEILINGS = {"secret_leaks", "unsupported_explanations", "score_language", "calls_over_budget", "stored_leaks"}
ARCH = uuid.UUID(int=700)
PLACEHOLDER = re.compile(r"\{(change|group):([a-z0-9-]+)\}")

# The pair most scenarios edit: a client, an API (with a credential it keeps in its settings), a database.
SHOP: dict[str, Any] = {
    "schema_version": 1,
    "name": "Shop",
    "nodes": [
        {"id": "web", "kind": "client", "name": "Web"},
        {
            "id": "api",
            "kind": "service",
            "name": "Orders API",
            "configuration": {"values": {"replicas": 2}, "extra": {"api_key": "eval-secret-before-91a"}},
        },
        {
            "id": "db",
            "kind": "database",
            "name": "Orders DB",
            "component": "databases/postgresql",
            "technology": {"name": "postgresql", "version": "16"},
        },
    ],
    "connections": [
        {"id": "web-api", "source_id": "web", "target_id": "api", "kind": "request", "protocol": "https"},
        {
            "id": "api-db",
            "source_id": "api",
            "target_id": "db",
            "kind": "data_access",
            "protocol": "postgresql",
        },
    ],
}


def _merge(into: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(into)
    for key, value in patch.items():
        nested = isinstance(value, dict) and isinstance(into.get(key), dict)
        result[key] = _merge(into[key], value) if nested else value
    return result


def _edited(base: dict[str, Any], edits: Sequence[dict[str, Any]]) -> dict[str, Any]:
    ir: dict[str, Any] = json.loads(json.dumps(base))
    for edit in edits:
        if "node" in edit:
            ir["nodes"] = [_merge(n, edit["merge"]) if n["id"] == edit["node"] else n for n in ir["nodes"]]
        if "add_node" in edit:
            ir["nodes"].append(edit["add_node"])
        if "add_connection" in edit:
            ir["connections"].append(edit["add_connection"])
    return ir


def _decision(spec: dict[str, Any]) -> Decision:
    return Decision(
        id=uuid.UUID(int=800 + spec["number"]), project_id=PROJECT.id, architecture_id=ARCH,
        number=spec["number"], title=spec["title"], status=DecisionStatus(spec.get("status", "accepted")),
        context="Recorded for the evaluation.", options=(), created_by_user_id=None, created_at=AT,
        related_element_ids=tuple(spec["elements"]),
    )  # fmt: skip


def _resolved(ir: ArchitectureIR, number: int) -> ResolvedState:
    compared = ComparedState(StateRef.revision(ARCH, number), content_hash(ir), f"Shop r{number}")
    return ResolvedState(compared, ir, ARCH, number)


def _bound(value: Any, diff: ArchitectureDiff) -> Any:
    """A recorded output's ``{change:<element>}`` and ``{group:<element>}``, as this diff's ids."""
    if isinstance(value, dict):
        return {k: _bound(v, diff) for k, v in value.items()}
    if isinstance(value, list):
        return [_bound(v, diff) for v in value]
    if not isinstance(value, str):
        return value

    def bind(match: re.Match[str]) -> str:
        kind, element = match.groups()
        change = next(c for c in diff.semantic.changes if c.element_id == element)
        if kind == "change":
            return change.id
        return next(g.id for g in diff.semantic.groups if change.id in g.change_ids)

    return PLACEHOLDER.sub(bind, value)


def _explanation(spec: Any) -> Any:
    """``{"explain": <element>, "patch": {...}}``: a grounded explanation of that element's change."""
    if not (isinstance(spec, dict) and "explain" in spec):
        return spec
    cited = [{"basis": "change", "ref": f"{{change:{spec['explain']}}}"}]
    base = {
        "summary": {"text": "The target changes this element.", "groundings": cited, "inferred": False},
        "groups": [],
        "tradeoffs": [],
        "requirements": [],
        "risks": [{"text": "This may need a closer look.", "groundings": [], "inferred": True}],
        "questions": [{"text": "Was this change intended?", "groundings": cited, "inferred": False}],
        "unknowns": [],
    }
    return base | spec.get("patch", {})


@dataclass(frozen=True, slots=True)
class Outcome:
    scenario: dict[str, Any]
    diff: ArchitectureDiff
    run: ExplanationRun
    requests: tuple[StructuredRequest, ...]
    passages: tuple[Passage, ...]
    citable: dict[str, frozenset[str]]  # what the explanation's context listed, by basis

    @property
    def expected(self) -> dict[str, Any]:
        expected: dict[str, Any] = self.scenario["expected"]
        return expected


def _engines() -> DiffEngines:
    capacity = DeterministicCapacityEngine()
    return DiffEngines(
        DeterministicValidationEngine(catalog=default_catalog()),
        DeterministicReliabilityEngine(),
        DeterministicSecurityEngine(),
        DeterministicObservabilityEngine(),
        capacity,
        DeterministicCostEngine(capacity=capacity),
    )


async def run_scenario(scenario: dict[str, Any]) -> Outcome:
    base_edits = scenario.get("base_edits", [])
    base_ir = from_dict(_edited(SHOP, base_edits))
    target_ir = from_dict(_edited(SHOP, [*base_edits, *scenario.get("edits", [])]))
    requirements: tuple[Requirement, ...] = tuple(
        _requirement(n, spec) for n, spec in enumerate(scenario.get("requirements", []), start=1)
    )
    decisions = tuple(_decision(d) for d in scenario.get("decisions", []))
    base, target = _resolved(base_ir, 1), _resolved(target_ir, 2)
    inputs = DiffInputs(ImpactInputs(requirements), {r.id: r for r in requirements}, decisions)
    outcome = DeterministicDiffEngine(_engines()).compare(base, target, inputs)
    request = DiffRequest(
        base.compared.ref, target.compared.ref, context=scenario.get("context"), explain=True
    )
    diff = ArchitectureDiff(
        uuid.UUID(int=901), PROJECT.id, request, base.compared, target.compared, outcome.semantic, USER, AT,
        outcome.requirements, outcome.decisions, outcome.engines, (), outcome.unknowns,
    )  # fmt: skip
    passages = _passages(scenario.get("passages", []))
    llm = RecordedLlm([_bound(_explanation(o), diff) for o in scenario.get("outputs", [])])

    async def retrieve(query: RetrievalQuery) -> RetrievalResult:
        return RetrievalResult(passages if query.text else (), 1)

    interpreter = DiffInterpretation(DiffExplanationAgent(llm, clock=lambda: 0.0))
    budget = ExplanationBudget()
    run = await interpreter.interpret(diff, retrieve, budget, run_id=uuid.UUID(int=902), user_id=USER, now=AT)
    context = assemble(diff, merged([passages], budget), budget).context
    citable = {b.value: refs for b, refs in context.citable.items()} if context else {}
    return Outcome(scenario, diff, run, tuple(llm.requests), passages, citable)


# --- what each scenario is checked for ------------------------------------------------------------------


def _changes(outcome: Outcome) -> bool:
    found = sorted([c.element.value, c.element_id, c.change.value] for c in outcome.diff.semantic.changes)
    return found == sorted(outcome.expected["changes"])


def _classified(outcome: Outcome) -> bool:
    by_element = {c.element_id: {k.value for k in c.classes} for c in outcome.diff.semantic.changes}
    wanted: dict[str, list[str]] = outcome.expected.get("classes", {})
    return all(set(classes) <= by_element.get(element, set()) for element, classes in wanted.items())


def _grouped(outcome: Outcome) -> bool:
    semantic = outcome.diff.semantic
    elements = {c.id: c.element_id for c in semantic.changes}
    found = sorted(sorted(elements[i] for i in g.change_ids) for g in semantic.groups)
    return found == sorted(sorted(g) for g in outcome.expected.get("groups", []))


def _impacts(outcome: Outcome) -> bool:
    expected, diff = outcome.expected, outcome.diff
    relations = {r.reference: r.relation.value for r in diff.requirements}
    introduced = {
        e.engine
        for e in diff.engines
        if e.status is ImpactStatus.EVALUATED and e.of_state(FindingState.INTRODUCED)
    }
    return (
        relations == expected.get("requirements", {})
        and sorted(d.reference for d in diff.decisions) == sorted(expected.get("decisions", []))
        and set(expected.get("introduced", [])) <= introduced
    )


def _explained(outcome: Outcome) -> bool:
    run, wanted = outcome.run, outcome.expected["explanation"]
    failure = run.failure.value if run.failure else None
    return (
        run.status.value == wanted["status"]
        and failure == wanted.get("failure")
        and run.usage.model_calls == wanted.get("model_calls", 0)
        and set(wanted.get("rejections", [])) <= {r.code for r in run.rejections}
    )


def _grounded(outcome: Outcome) -> bool:
    explanation = outcome.run.explanation
    if explanation is None:
        return True
    return all(
        g.ref in outcome.citable.get(g.basis.value, frozenset())
        for s in explanation.statements()
        for g in s.groundings
    )


def _contained(request: StructuredRequest) -> bool:
    opened = request.user_content.count(f"<{DATA_TAG} section=")
    return request.system == SYSTEM_PROMPT and opened == request.user_content.count(f"</{DATA_TAG}>")


def _read_back(outcome: Outcome) -> bool:
    diff, run = outcome.diff, outcome.run
    stored_run = explanation_run_from(explanation_document(PROJECT.id, run))
    return diff_from(diff_document(diff)) == diff and stored_run == run


def _secret_leaks(outcome: Outcome) -> int:
    stored = json.dumps(diff_document(outcome.diff), default=str)
    stored += json.dumps(explanation_document(PROJECT.id, outcome.run), default=str)
    sent = " ".join(r.user_content for r in outcome.requests)
    return sum(1 for s in outcome.expected.get("secrets", []) if s in stored or s in sent)


def _stored_leaks(outcome: Outcome) -> int:
    stored = json.dumps(explanation_document(PROJECT.id, outcome.run), default=str)
    texts = [SYSTEM_PROMPT[:200], *(p.text for p in outcome.passages)]
    return sum(1 for t in texts if t and t in stored)


def _scored(outcome: Outcome) -> bool:
    explanation = outcome.run.explanation
    return explanation is not None and any(SCORE.search(s.text) for s in explanation.statements())


@dataclass(frozen=True, slots=True)
class Evaluation:
    outcomes: tuple[Outcome, ...]

    def metrics(self) -> dict[str, float | int]:
        outcomes = self.outcomes
        refusing = [o for o in outcomes if o.expected["explanation"].get("rejections")]
        refused = [
            o
            for o in refusing
            if set(o.expected["explanation"]["rejections"]) <= {r.code for r in o.run.rejections}
        ]
        requests = [r for o in outcomes for r in o.requests]
        completed = [o for o in outcomes if o.run.explanation is not None]

        def share(passed: int, of: int) -> float:
            return round(passed / of, 4) if of else 1.0

        return {
            "scenarios": len(outcomes),
            "change_accuracy": share(sum(map(_changes, outcomes)), len(outcomes)),
            "classification_accuracy": share(sum(map(_classified, outcomes)), len(outcomes)),
            "grouping_accuracy": share(sum(map(_grouped, outcomes)), len(outcomes)),
            "impact_accuracy": share(sum(map(_impacts, outcomes)), len(outcomes)),
            "explanation_accuracy": share(sum(map(_explained, outcomes)), len(outcomes)),
            "rejection_accuracy": share(len(refused), len(refusing)),
            "grounding_integrity": share(sum(map(_grounded, completed)), len(completed)),
            "injection_containment": share(sum(map(_contained, requests)), len(requests)),
            "record_integrity": share(sum(map(_read_back, outcomes)), len(outcomes)),
            "secret_leaks": sum(map(_secret_leaks, outcomes)),
            "unsupported_explanations": sum(
                1 for o in completed if o.expected["explanation"]["status"] != "completed"
            ),
            "score_language": sum(map(_scored, completed)),
            "calls_over_budget": sum(1 for o in outcomes if o.run.usage.model_calls > 2),
            "stored_leaks": sum(map(_stored_leaks, outcomes)),
        }

    def misses(self) -> list[str]:
        checks = (_changes, _classified, _grouped, _impacts, _explained, _grounded, _read_back)
        return [
            f"{o.scenario['id']}: {c.__name__.strip('_')}" for o in self.outcomes for c in checks if not c(o)
        ]


def load_scenarios(dataset: Path = DATASET) -> list[dict[str, Any]]:
    lines = (dataset / "scenarios.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def evaluate(dataset: Path = DATASET) -> Evaluation:
    async def everything() -> tuple[Outcome, ...]:
        return tuple([await run_scenario(s) for s in load_scenarios(dataset)])

    return Evaluation(asyncio.run(everything()))


def thresholds() -> dict[str, float | int]:
    loaded: dict[str, float | int] = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    return loaded


def check(metrics: dict[str, float | int]) -> list[str]:
    """What fell below its threshold (or rose above its ceiling)."""
    problems = []
    for name, limit in thresholds().items():
        value = metrics[name]
        if (name in CEILINGS and value > limit) or (name not in CEILINGS and value < limit):
            problems.append(f"{name}: {value} (threshold {limit})")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Architecture diff evaluation")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    evaluation = evaluate()
    metrics = evaluation.metrics()
    if args.json:
        print(json.dumps(metrics, indent=2, sort_keys=True))  # noqa: T201 - a command-line report
    else:
        for name, value in metrics.items():
            print(f"{name:26} {value}")  # noqa: T201
        for miss in evaluation.misses():
            print(f"miss: {miss}")  # noqa: T201
    if args.check:
        problems = check(metrics)
        for problem in problems:
            print(problem, file=sys.stderr)  # noqa: T201
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
