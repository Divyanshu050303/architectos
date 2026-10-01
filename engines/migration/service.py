"""The migration planning engine behind the service's port: resolve the exact target (a later revision,
or an evolution candidate applied to the source revision), classify the architecture diff, generate
the plan and bring in the stored analyses as evidence. Pure: no I/O, nothing mutated."""

from collections.abc import Mapping

from core.domain.migrations.plans import MigrationProposal
from core.domain.migrations.ports import PlanningInputs, TargetCandidate

from .changes import analyze, candidate_target, revision_target
from .evidence import integrate
from .patternbook import default_registry
from .patterns import PlanningContext, Registry
from .planner import PLANNER, generate


class MigrationEngine:
    def __init__(self, registry: Registry | None = None) -> None:
        self._registry = registry or default_registry()

    def plan(self, inputs: PlanningInputs) -> MigrationProposal:
        source, target = inputs.source, inputs.target
        if isinstance(target, TargetCandidate):
            reference, target_ir = candidate_target(
                source, inputs.source_ir, target.analysis_id, target.candidate
            )
            candidate = target.candidate
        else:
            reference, target_ir = revision_target(source, target.number, target.content_hash), target.ir
            candidate = None
        analysis = analyze(inputs.source_ir, target_ir, source, reference)
        context = PlanningContext(inputs.source_ir, target_ir, analysis, inputs.request)
        return integrate(generate(context, self._registry), inputs.analyses, candidate)

    def models(self) -> Mapping[str, int]:
        return {**self._registry.versions(), PLANNER[0]: PLANNER[1]}
