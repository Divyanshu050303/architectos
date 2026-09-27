"""Per-analysis memoization of an engine an evolution analysis calls many times with the same inputs:
every candidate's impact evaluates the unchanged baseline again. The engines are pure, so the same
content, request and requirements give the same result; the memo lives for one analysis only, never
across analyses."""

import json
from collections.abc import Mapping
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.domain.reliability.analyses import ReliabilityAnalysisRequest
from core.domain.reliability.ports import ReliabilityEngine
from core.domain.reliability.results import ReliabilityResult
from core.domain.requirements.entities import Requirement
from core.domain.validation.options import RevisionInfo


class MemoReliability:
    def __init__(self, engine: ReliabilityEngine) -> None:
        self._engine = engine
        self._results: dict[tuple[str, str, tuple[tuple[str, int], ...]], ReliabilityResult] = {}

    def analyze(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        request: ReliabilityAnalysisRequest,
        requirements: tuple[Requirement, ...],
    ) -> ReliabilityResult:
        key = (
            revision.content_hash,
            json.dumps(request.inputs(), sort_keys=True, default=str),
            tuple((str(r.id), r.version) for r in requirements),
        )
        if key not in self._results:
            self._results[key] = self._engine.analyze(ir, revision, request, requirements)
        return self._results[key]

    def models(self) -> tuple[Mapping[str, Any], ...]:
        return self._engine.models()
