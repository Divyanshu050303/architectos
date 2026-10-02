"""The deterministic discovery engine: a request's artifacts read by the source adapters, normalized,
mapped to the component catalog, related, and proposed as an Architecture IR — one result, with the
version of every extractor and rule that produced it.

Synchronous and bounded: the request's limits (artifacts, bytes) and each adapter's limits (documents,
depth, nodes, findings) bound the work; a discovery producing more entities, relationships or findings
than a result holds is refused rather than truncated silently. Nothing is executed, evaluated, rendered
or fetched, and no architecture is written: the proposal waits for a person.
"""

from collections.abc import Mapping

from core.domain.components.repository import ComponentCatalog
from core.domain.discovery.errors import InvalidDiscoveryRequest
from core.domain.discovery.results import (
    MAX_DIAGNOSTICS,
    MAX_ENTITIES,
    MAX_FINDINGS,
    MAX_RELATIONSHIPS,
    DiscoveryResult,
    Proposal,
)
from core.domain.discovery.runs import DiscoveryRequest, ReviewDecision

from . import mapping, normalize, proposal, relationships
from .adapters import SourceAdapter, read_artifacts
from .sources import ADAPTERS

RULES = (
    *normalize.RULES.values(),
    mapping.CATALOG_RULE,
    mapping.CONFIGURATION_RULE,
    relationships.RULE,
    proposal.RULE,
)


def _versions(rules: tuple[str, ...]) -> dict[str, int]:
    return {name: int(version) for name, _, version in (r.partition("@") for r in rules)}


def _within(name: str, found: int, limit: int) -> None:
    if found > limit:
        raise InvalidDiscoveryRequest(
            details={"field": "artifacts", "reason": f"too_many_{name}", "limit": limit}
        )


class DeterministicDiscoveryEngine:
    def __init__(self, catalog: ComponentCatalog, adapters: tuple[SourceAdapter, ...] = ADAPTERS) -> None:
        self._catalog = catalog
        self._adapters = adapters

    def discover(self, request: DiscoveryRequest, name: str | None = None) -> DiscoveryResult:
        extraction = read_artifacts(request, self._adapters)
        normalized = normalize.normalize(extraction)
        entities = mapping.candidates(normalized.entities, self._catalog)
        related = relationships.relationships(extraction, normalized.entities)
        diagnostics = (*extraction.diagnostics, *normalized.diagnostics)
        _within("findings", len({f.id for f in extraction.findings}), MAX_FINDINGS)
        _within("entities", len(entities), MAX_ENTITIES)
        _within("relationships", len(related), MAX_RELATIONSHIPS)
        _within("diagnostics", len(set(diagnostics)), MAX_DIAGNOSTICS)
        proposed = proposal.propose(entities, related, self._catalog, None, name or request.label)
        return DiscoveryResult(
            artifacts=extraction.artifacts,
            findings=extraction.findings,
            entities=entities,
            relationships=related,
            diagnostics=diagnostics,
            proposed=proposed.architecture,
            validation=proposed.validation,
            elements=proposed.elements,
            extractors=dict(extraction.extractors) | _versions(RULES),
        )

    def propose(
        self, result: DiscoveryResult, decisions: tuple[ReviewDecision, ...], name: str | None = None
    ) -> Proposal:
        return proposal.propose(
            result.entities, result.relationships, self._catalog, proposal.latest(decisions), name
        )

    def versions(self) -> Mapping[str, int]:
        return {a.source_type.value: a.version for a in self._adapters} | _versions(RULES)
